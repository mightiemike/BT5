The code path is fully traceable. Let me walk through each step precisely.

---

**Step 1: `parse_softfork_arguments` passes `self.dialect.flags()` to `uint_atom::<4>`** [1](#0-0) 

The extension atom is parsed via `uint_atom::<4>` with the dialect's full flags, which includes `CANONICAL_INTS` when running under `MEMPOOL_MODE`.

---

**Step 2: `uint_atom` behavior diverges on `[0x00, 0x01]` depending on `CANONICAL_INTS`** [2](#0-1) 

- **With `CANONICAL_INTS` (MEMPOOL_MODE):** `buf[0] == 0`, `buf.len() >= 2`, `(buf[1] & 0x80) == 0` → all true → returns `Err("softfork requires u32 arg with no leading zeros")`.
- **Without `CANONICAL_INTS` (consensus mode):** strips leading zeros → `buf = [0x01]` → returns `Ok(1)`.

---

**Step 3: Error propagation in `apply_op` diverges on `allow_unknown_ops()`** [3](#0-2) 

- **Consensus mode** (`allow_unknown_ops()` = `true`): on `Err`, pushes nil and returns `expected_cost` — **accepts**.
- **MEMPOOL_MODE** (`allow_unknown_ops()` = `false`): on `Err`, propagates the error — **rejects**.

But critically, in consensus mode `parse_softfork_arguments` does **not** fail for `[0x00, 0x01]` — it succeeds and returns `OperatorSet::Keccak`. The inner keccak program is then entered and executed normally.

---

**Step 4: `softfork_extension(1)` returns `Keccak`, not `Default`** [4](#0-3) 

So in consensus mode, `[0x00, 0x01]` → `uint_atom` returns `Ok(1)` → `softfork_extension(1)` returns `OperatorSet::Keccak` → `Keccak != Default` → `parse_softfork_arguments` returns `Ok((Keccak, prg, env))` → softfork guard is entered → keccak256 is available inside.

---

**Step 5: `MEMPOOL_MODE` definition confirms both flags are set** [5](#0-4) 

Both `CANONICAL_INTS` and `NO_UNKNOWN_OPS` are set in `MEMPOOL_MODE`, which is what drives both sides of the divergence.

---

**Conclusion:**

The split is real and concrete:

| Mode | Extension atom `[0x00, 0x01]` | Result |
|---|---|---|
| Consensus (`ClvmFlags::empty()`) | `uint_atom` strips zero → `Ok(1)` → Keccak guard entered | **Accepts** (if inner program succeeds) |
| MEMPOOL_MODE | `uint_atom` rejects leading zero → `Err` → propagated | **Rejects** |

A farmer can craft a softfork program with extension atom `[0x00, 0x01]`, include it directly in a block (bypassing mempool), and all consensus nodes will accept the block. Mempool nodes would have rejected the transaction. This is a concrete, locally testable consensus/mempool split.

---

### Title
Non-Canonical Softfork Extension Atom Accepted by Consensus, Rejected by Mempool — (`src/run_program.rs`, `src/op_utils.rs`)

### Summary
`parse_softfork_arguments` passes `self.dialect.flags()` (which includes `CANONICAL_INTS` in `MEMPOOL_MODE`) to `uint_atom::<4>` when parsing the softfork extension argument. This causes mempool to reject extension atoms with leading zeros (e.g., `[0x00, 0x01]`), while consensus mode strips the leading zero and successfully resolves extension=1 (Keccak), entering the softfork guard and executing the inner program.

### Finding Description
In `src/run_program.rs`, `parse_softfork_arguments` calls `uint_atom::<4>(self.allocator, extension, "softfork", self.dialect.flags())`. [6](#0-5) 

In `src/op_utils.rs`, when `CANONICAL_INTS` is set and the atom is `[0x00, 0x01]`: `buf[0] == 0`, `buf.len() >= 2`, and `(buf[1] & 0x80) == 0` are all true, so an error is returned. [7](#0-6) 

Without `CANONICAL_INTS`, the leading zero is stripped and `Ok(1)` is returned. [8](#0-7) 

Back in `apply_op`, the error from `parse_softfork_arguments` is propagated in mempool mode (`allow_unknown_ops()` = false) but in consensus mode the function never errors — it returns `Ok((Keccak, prg, env))` and the guard is entered. [3](#0-2) 

### Impact Explanation
A transaction containing a softfork program with extension atom `[0x00, 0x01]` (non-canonical encoding of 1):
- Is **accepted** by consensus nodes (extension=1 → Keccak guard entered, inner program runs)
- Is **rejected** by mempool nodes (non-canonical encoding error)

A block producer can include such a transaction directly in a block, bypassing mempool. All full nodes validate blocks in consensus mode and will accept the block. This is a concrete consensus/mempool split: the mempool enforces a stricter rule that is not symmetric with consensus behavior for this specific argument.

### Likelihood Explanation
Exploiting this requires a block producer (farmer) who bypasses the mempool. The encoding `[0x00, 0x01]` is trivially constructable. No special privileges beyond block production are needed. The split is deterministic and reproducible.

### Recommendation
The softfork extension argument should be parsed with a fixed, mode-independent canonicality check — either always requiring canonical encoding (rejecting `[0x00, 0x01]` in both modes) or always stripping leading zeros (accepting it in both modes). The simplest fix is to call `uint_atom::<4>` for the extension argument with `ClvmFlags::empty()` (or a dedicated flag), decoupling it from the per-mode `CANONICAL_INTS` flag. Alternatively, add an explicit pre-check that rejects non-canonical extension atoms in consensus mode as well.

### Proof of Concept
```rust
// Construct: (softfork cost [0x00, 0x01] (keccak256 ...) env)
// Run under ClvmFlags::empty() → succeeds (extension=1, keccak guard entered)
// Run under MEMPOOL_MODE → Err("softfork requires u32 arg with no leading zeros")
let extension_atom = allocator.new_atom(&[0x00, 0x01]).unwrap(); // non-canonical 1
// ... build softfork args list with this extension atom ...
let consensus = run_program(&mut allocator, &ChiaDialect::new(ClvmFlags::empty()), prg, env, u64::MAX);
let mempool  = run_program(&mut allocator, &ChiaDialect::new(MEMPOOL_MODE), prg, env, u64::MAX);
assert!(consensus.is_ok());
assert!(mempool.is_err()); // "softfork requires u32 arg with no leading zeros"
```

### Citations

**File:** src/run_program.rs (L354-368)
```rust
    fn parse_softfork_arguments(&self, args: NodePtr) -> Result<(OperatorSet, NodePtr, NodePtr)> {
        let [_cost, extension, program, env] = get_args::<4>(self.allocator, args, "softfork")?;

        let extension = self.dialect.softfork_extension(uint_atom::<4>(
            self.allocator,
            extension,
            "softfork",
            self.dialect.flags(),
        )? as u32);
        if extension == OperatorSet::Default {
            Err(EvalErr::UnknownSoftforkExtension)
        } else {
            Ok((extension, program, env))
        }
    }
```

**File:** src/run_program.rs (L400-413)
```rust
            let (ext, prg, env) = match self.parse_softfork_arguments(operand_list) {
                Ok(ret_values) => ret_values,
                Err(err) => {
                    if self.dialect.allow_unknown_ops() {
                        // In this case, we encountered a softfork invocation
                        // that doesn't pass the correct arguments.
                        // if we're in consensus mode, we have to accept this as
                        // something we don't understand
                        self.push(self.allocator.nil())?;
                        return Ok(expected_cost);
                    }
                    return Err(err);
                }
            };
```

**File:** src/op_utils.rs (L67-86)
```rust
            if flags.contains(ClvmFlags::CANONICAL_INTS) {
                // strip potential zero
                if buf[0] == 0 {
                    if buf.len() < 2 || (buf[1] & 0x80) == 0 {
                        return Err(EvalErr::InvalidOpArg(
                            args,
                            format!(
                                "{op_name} requires u{0} arg with no leading zeros",
                                SIZE * 8
                            ),
                        ));
                    }
                    buf = &buf[1..];
                }
            } else {
                // strip leading zeros
                while !buf.is_empty() && buf[0] == 0 {
                    buf = &buf[1..];
                }
            }
```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L269-283)
```rust
    fn softfork_extension(&self, ext: u32) -> OperatorSet {
        match ext {
            // Extension 0 is for the BLS operators, and is still valid.
            // However, the extension doesn't add any addition opcodes,
            // because the BLS operators were hardforked into the main set.
            0 => OperatorSet::Bls,

            // Extension 1 is for the keccak256 operator.
            1 => OperatorSet::Keccak,

            // Extensions 2 and beyond are considered invalid by the mempool.
            // However, all future extensions are valid in consensus mode and reserved for future softforks.
            _ => OperatorSet::Default,
        }
    }
```

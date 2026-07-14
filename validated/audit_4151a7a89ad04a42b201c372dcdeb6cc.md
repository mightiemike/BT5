Now I have all the information needed to analyze this precisely. Let me trace the exact code path.

### Title
Consensus/Mempool Split via Non-Canonical Softfork Extension Atom — (`src/op_utils.rs`, `src/run_program.rs`)

### Summary

`parse_softfork_arguments` passes `self.dialect.flags()` directly into `uint_atom::<4>` when decoding the softfork extension number. In mempool mode `CANONICAL_INTS` is set, so a non-canonical atom such as `[0x00, 0x01]` (the two-byte encoding of integer 1) is rejected with an error. In consensus mode `CANONICAL_INTS` is absent, so the same atom is silently stripped of its leading zero and decoded as `1`, causing the keccak softfork guard to be entered and the program to execute normally. The error-handling branch in `apply_op` then diverges: consensus mode swallows the error (returns nil), but mempool mode propagates it as a hard failure. The result is a concrete, locally-testable consensus/mempool split.

---

### Finding Description

**Step 1 — `uint_atom::<4>` with `CANONICAL_INTS` set (mempool)**

For atom bytes `[0x00, 0x01]`:

```
buf[0] == 0  →  true
buf.len() < 2  →  false
(buf[1] & 0x80) == 0  →  true   // 0x01 & 0x80 == 0
→ Err("softfork requires u32 arg with no leading zeros")
``` [1](#0-0) 

**Step 2 — `uint_atom::<4>` without `CANONICAL_INTS` (consensus)**

```
else branch: strip leading zeros
buf = [0x01]
buf.len() = 1 ≤ SIZE = 4  →  Ok(1)
``` [2](#0-1) 

**Step 3 — `parse_softfork_arguments` passes `dialect.flags()` verbatim**

The flags used for the extension atom parse are exactly the dialect's runtime flags, so the `CANONICAL_INTS` difference between modes is fully inherited here. [3](#0-2) 

**Step 4 — `apply_op` error-handling diverges by mode**

When `parse_softfork_arguments` returns `Err`:

- **Consensus** (`allow_unknown_ops()` → `true`): pushes nil, returns `expected_cost` — the softfork is silently accepted as a no-op.
- **Mempool** (`allow_unknown_ops()` → `false`, because `NO_UNKNOWN_OPS` ∈ `MEMPOOL_MODE`): propagates the error — the entire program fails. [4](#0-3) 

`MEMPOOL_MODE` is defined as: [5](#0-4) 

`allow_unknown_ops` is: [6](#0-5) 

---

### Impact Explanation

A transaction whose puzzle contains `(softfork cost [0x00 0x01] program env)` — where `[0x00, 0x01]` is the non-canonical two-byte encoding of extension 1 — is:

- **Valid on-chain (consensus)**: `uint_atom` strips the leading zero, decodes `1`, enters the keccak softfork guard, runs the program, and returns nil. The transaction is accepted.
- **Invalid in the mempool**: `uint_atom` rejects the non-canonical encoding, `allow_unknown_ops()` is false, and the error propagates. The transaction is rejected before execution.

This is a direct consensus/mempool split. A miner can include such a transaction in a block directly (bypassing the mempool), and full nodes will accept it on-chain while the mempool would have rejected it. This breaks the invariant that extension-1 keccak programs behave identically in both modes.

---

### Likelihood Explanation

The attack requires only crafting a CLVM puzzle with a two-byte extension atom. No special privileges, compromised nodes, or social engineering are needed. The split is deterministic and reproducible with a single Rust unit test.

---

### Recommendation

In `parse_softfork_arguments`, strip `CANONICAL_INTS` from the flags passed to `uint_atom` when parsing the extension argument, or — preferably — parse the extension atom with a dedicated function that always strips leading zeros regardless of mode. The extension argument is a protocol-level selector, not a user-supplied integer, and its canonicality should not be mode-dependent.

---

### Proof of Concept

```rust
// Consensus mode: [0x00, 0x01] → Ok(1) → Keccak guard entered
let mut a = Allocator::new();
let ext_atom = a.new_atom(&[0x00, 0x01]).unwrap();
assert_eq!(
    uint_atom::<4>(&a, ext_atom, "softfork", ClvmFlags::empty()),
    Ok(1)
);

// Mempool mode: [0x00, 0x01] → Err (non-canonical)
assert!(
    uint_atom::<4>(&a, ext_atom, "softfork", MEMPOOL_MODE).is_err()
);
```

A full `run_program` test would build `(softfork <cost> (0x00 0x01) <keccak-program> <env>)`, run it under `ClvmFlags::empty()` (succeeds), then under `MEMPOOL_MODE` (fails), confirming the split.

### Citations

**File:** src/op_utils.rs (L67-78)
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
```

**File:** src/op_utils.rs (L81-86)
```rust
            } else {
                // strip leading zeros
                while !buf.is_empty() && buf[0] == 0 {
                    buf = &buf[1..];
                }
            }
```

**File:** src/run_program.rs (L357-362)
```rust
        let extension = self.dialect.softfork_extension(uint_atom::<4>(
            self.allocator,
            extension,
            "softfork",
            self.dialect.flags(),
        )? as u32);
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

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L285-287)
```rust
    fn allow_unknown_ops(&self) -> bool {
        !self.flags.contains(ClvmFlags::NO_UNKNOWN_OPS)
    }
```

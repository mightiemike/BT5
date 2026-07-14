The vulnerability is real and concrete. Let me trace every step precisely.

---

### Title
Softfork Extension Non-Canonical Encoding Creates Consensus/Mempool Split via `uint_atom` CANONICAL_INTS Divergence — (`src/run_program.rs`, `src/op_utils.rs`)

### Summary

A CLVM program (including one deserialized from a serde_2026 blob) can encode the softfork extension argument as a non-canonical atom — e.g., `[0x00, 0x01]` for extension 1 — causing `uint_atom` to behave differently under `MEMPOOL_MODE` vs. consensus mode. In consensus mode the leading zero is silently stripped and extension 1 (keccak256) is enabled; in mempool mode the non-canonical encoding is rejected with `InvalidOpArg`. This is a concrete, locally testable consensus/mempool split.

---

### Finding Description

**`MEMPOOL_MODE`** is defined as: [1](#0-0) 

```
CANONICAL_INTS | NO_UNKNOWN_OPS | LIMIT_HEAP | DISABLE_OP | LIMIT_SOFTFORK
```

**`uint_atom` in `src/op_utils.rs`** has two distinct code paths depending on `CANONICAL_INTS`: [2](#0-1) 

- **With `CANONICAL_INTS`** (mempool): if `buf[0] == 0` and `buf[1] & 0x80 == 0`, returns `Err(InvalidOpArg(..., "softfork requires u32 arg with no leading zeros"))`.
- **Without `CANONICAL_INTS`** (consensus): strips all leading zero bytes silently, then returns the numeric value.

For the atom `[0x00, 0x01]`:
- `buf[0] == 0` → true; `buf[1] & 0x80 == 0x01 & 0x80 == 0` → true → **mempool: error**.
- Consensus strips `0x00`, gets `[0x01]`, returns `Ok(1)` → **consensus: extension 1 (keccak256) enabled**.

**`parse_softfork_arguments`** calls `uint_atom::<4>` with `self.dialect.flags()` on the extension argument: [3](#0-2) 

**`apply_op`** then handles the error from `parse_softfork_arguments` differently based on `allow_unknown_ops()`: [4](#0-3) 

- **Consensus** (`allow_unknown_ops()` = true, no `NO_UNKNOWN_OPS`): `parse_softfork_arguments` **succeeds** (returns `OperatorSet::Keccak`) → keccak256 is enabled inside the guard → program executes.
- **Mempool** (`allow_unknown_ops()` = false, has `NO_UNKNOWN_OPS`): `parse_softfork_arguments` **fails** → `return Err(err)` → transaction rejected.

`allow_unknown_ops()` is: [5](#0-4) 

`softfork_extension(1)` maps to `OperatorSet::Keccak`: [6](#0-5) 

The serde_2026 deserializer (`deserialize_2026_body_from_stream`) reads atom bytes verbatim with no canonicalization, so a blob encoding `[0x00, 0x01]` as the extension atom is fully valid input: [7](#0-6) 

---

### Impact Explanation

A farmer/validator can directly include a block containing a CLVM spend whose softfork extension argument is `[0x00, 0x01]`. Consensus nodes accept the block (keccak256 executes successfully). Mempool nodes reject the same transaction with `InvalidOpArg`. This is a **consensus/mempool split**: the transaction bypasses mempool validation entirely, and any keccak256-dependent puzzle logic executes on-chain without mempool pre-screening.

---

### Likelihood Explanation

The crafted atom `[0x00, 0x01]` is trivially encodable in any CLVM serialization format including serde_2026. A farmer colluding with a transaction author (or a farmer running custom software) can include such a spend directly in a block. No special privileges beyond block production are required.

---

### Recommendation

In `parse_softfork_arguments`, parse the extension argument with `CANONICAL_INTS` unconditionally (not inherited from `self.dialect.flags()`), or add an explicit leading-zero check on the extension atom before calling `uint_atom`. The cost argument at line 385 has the same issue and should be fixed consistently.

---

### Proof of Concept

```rust
// Consensus mode: ClvmFlags::empty() — no CANONICAL_INTS
// Mempool mode:   MEMPOOL_MODE       — includes CANONICAL_INTS | NO_UNKNOWN_OPS

// Extension atom [0x00, 0x01] = non-canonical encoding of 1 (keccak256 extension)
// Cost atom [0x01] = canonical encoding of 1 (adjust to match actual program cost)

// Program: (softfork (q . <cost>) (q . 0x0001) (q . <keccak_prg>) (q . ()))
// In consensus: uint_atom strips 0x00 → extension=1 → keccak256 enabled → Ok(())
// In mempool:   uint_atom rejects 0x0001 → InvalidOpArg → Err

// Divergence: consensus accepts block, mempool rejects transaction.
```

The exact divergence is:
- `uint_atom::<4>(&a, ext_node, "softfork", ClvmFlags::empty())` → `Ok(1)` for `[0x00, 0x01]`
- `uint_atom::<4>(&a, ext_node, "softfork", ClvmFlags::CANONICAL_INTS)` → `Err(InvalidOpArg(..., "softfork requires u32 arg with no leading zeros"))` for `[0x00, 0x01]` [2](#0-1)

### Citations

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

**File:** src/chia_dialect.rs (L285-287)
```rust
    fn allow_unknown_ops(&self) -> bool {
        !self.flags.contains(ClvmFlags::NO_UNKNOWN_OPS)
    }
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

**File:** src/serde_2026/de.rs (L39-71)
```rust
pub fn deserialize_2026_body_from_stream<R: Read>(
    allocator: &mut Allocator,
    reader: &mut R,
    max_atom_len: usize,
    strict: bool,
) -> Result<NodePtr> {
    let mut atoms: Vec<NodePtr> = Vec::new();
    let group_count = checked_usize(read_varint(reader, strict)?)?;
    let mut buf: Vec<u8> = Vec::new();

    for _ in 0..group_count {
        let length_val = read_varint(reader, strict)?;
        let (length, count) = if length_val < 0 {
            if length_val == i64::MIN {
                return Err(EvalErr::SerializationError);
            }
            (
                checked_bounded_usize(-length_val, max_atom_len)?,
                checked_usize(read_varint(reader, strict)?)?,
            )
        } else {
            (checked_bounded_usize(length_val, max_atom_len)?, 1)
        };
        if length == 0 || count == 0 {
            return Err(EvalErr::SerializationError);
        }
        buf.resize(length, 0);
        for _ in 0..count {
            reader
                .read_exact(&mut buf)
                .map_err(|_| EvalErr::SerializationError)?;
            atoms.push(allocator.new_atom(&buf)?);
        }
```

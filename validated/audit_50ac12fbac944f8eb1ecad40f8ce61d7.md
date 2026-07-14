The code evidence is sufficient to render a verdict. Let me trace the exact path.

---

**`uint_atom` behavior divergence (confirmed in `src/op_utils.rs`):**

Without `CANONICAL_INTS` (consensus, `ClvmFlags::empty()`): [1](#0-0) 

Leading zeros are silently stripped. `0x00000001` → `[0x01]` → returns `1`.

With `CANONICAL_INTS` (mempool, `MEMPOOL_MODE`): [2](#0-1) 

`buf[0] == 0`, `buf[1] == 0x00`, `(buf[1] & 0x80) == 0` → hard error: `"softfork requires u32 arg with no leading zeros"`.

**`MEMPOOL_MODE` includes `CANONICAL_INTS`:** [3](#0-2) 

**`softfork_extension(1)` returns `OperatorSet::Keccak`:** [4](#0-3) 

**`OperatorSet::Keccak` enables `ENABLE_KECCAK_OPS_OUTSIDE_GUARD` inside the guard:** [5](#0-4) 

---

### Title
Consensus/Mempool Split via Non-Canonical Softfork Extension Atom — (`src/op_utils.rs`, `src/run_program.rs`)

### Summary
`uint_atom::<4>` silently strips leading zeros when `CANONICAL_INTS` is absent (consensus mode), but rejects the same atom in mempool mode. A softfork call with extension `0x00000001` (non-canonical encoding of `1`) enters the keccak guard in consensus and is rejected outright in mempool, creating a confirmed consensus/mempool split.

### Finding Description
`parse_softfork_arguments` in `src/run_program.rs` calls `uint_atom::<4>(extension, dialect.flags())`. In consensus mode (`ClvmFlags::empty()`), `uint_atom` strips all leading zero bytes from the extension atom before comparing against `SIZE=4`. The atom `[0x00, 0x00, 0x00, 0x01]` (4 bytes, leading zeros) is reduced to `[0x01]` → value `1`. `softfork_extension(1)` returns `OperatorSet::Keccak`, and the keccak guard is entered successfully.

In mempool mode (`MEMPOOL_MODE` which includes `CANONICAL_INTS`), the same atom hits the branch at line 67–79 of `op_utils.rs`: `buf[0] == 0` and `buf[1] == 0x00` (MSB clear, not a sign byte), so the function returns an error immediately. The softfork guard is never entered; the transaction is rejected.

### Impact Explanation
A farmer/validator can include a block containing a CLVM program that uses `(softfork (q . N) (q . 0x00000001) (q keccak_prog) (q . ()))`. The mempool rejects this transaction (CANONICAL_INTS check fails on the extension atom). Consensus accepts it (leading zeros stripped, extension resolves to 1, keccak guard entered, keccak program executes). Any node validating the block in consensus mode accepts it; any node that pre-screened via mempool would have rejected it. This is a direct consensus/mempool split on keccak program acceptance.

### Likelihood Explanation
Exploiting this requires a farmer to directly include the transaction in a block, bypassing the mempool. This is a realistic and low-effort action for any block producer. The non-canonical encoding is trivially constructable.

### Recommendation
In `parse_softfork_arguments`, always pass `flags | ClvmFlags::CANONICAL_INTS` to `uint_atom` when parsing the softfork extension argument, regardless of the dialect's base flags. The extension index is a protocol-level identifier, not a user-facing integer, and must always be canonical to ensure mempool and consensus agree on which extension is being invoked.

### Proof of Concept
```rust
// Differential test sketch
let consensus_flags = ClvmFlags::empty();
let mempool_flags = MEMPOOL_MODE;

let mut a = Allocator::new();
// Non-canonical encoding of 1: [0x00, 0x00, 0x00, 0x01]
let ext_atom = a.new_atom(&[0x00, 0x00, 0x00, 0x01]).unwrap();

let consensus_result = uint_atom::<4>(&a, ext_atom, "softfork", consensus_flags);
// Ok(1) -> softfork_extension(1) -> OperatorSet::Keccak -> guard entered

let mempool_result = uint_atom::<4>(&a, ext_atom, "softfork", mempool_flags);
// Err("softfork requires u32 arg with no leading zeros") -> transaction rejected

assert!(consensus_result.is_ok());   // passes
assert!(mempool_result.is_err());    // passes
// Split confirmed: same atom, opposite outcomes
```

### Citations

**File:** src/op_utils.rs (L67-79)
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

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L144-154)
```rust
        let flags = self.flags
            | match extension {
                // This is the default set of operators, so no special flags need to be added.
                OperatorSet::Default => ClvmFlags::empty(),

                // Since BLS has been hardforked in universally, this has no effect.
                OperatorSet::Bls => ClvmFlags::empty(),

                // Keccak is allowed as if it were a default operator, inside of the softfork guard.
                OperatorSet::Keccak => ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD,
            };
```

**File:** src/chia_dialect.rs (L269-278)
```rust
    fn softfork_extension(&self, ext: u32) -> OperatorSet {
        match ext {
            // Extension 0 is for the BLS operators, and is still valid.
            // However, the extension doesn't add any addition opcodes,
            // because the BLS operators were hardforked into the main set.
            0 => OperatorSet::Bls,

            // Extension 1 is for the keccak256 operator.
            1 => OperatorSet::Keccak,

```

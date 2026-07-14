### Title
Caller-Controlled `RELAXED_BLS` Flag Bypasses G1/G2 Point Validation in `op_bls_g1_negate`/`op_bls_g2_negate` - (File: `src/bls_ops.rs`)

### Summary
The Python API `run_serialized_chia_program` accepts a raw `u32` flags parameter and silently accepts the `RELAXED_BLS` bit (`0x0008`) via `ClvmFlags::from_bits_truncate`. When set, this flag disables G1/G2 point validation in `op_bls_g1_negate` and `op_bls_g2_negate`, allowing invalid elliptic curve points to be processed without error. The flag is documented as a hard-fork flag that must only be enabled when the corresponding hard fork activates, but there is no enforcement preventing any Python caller from enabling it prematurely, causing consensus divergence between nodes.

### Finding Description
In `src/bls_ops.rs`, both `op_bls_g1_negate` and `op_bls_g2_negate` gate their point validation entirely on the caller-supplied `RELAXED_BLS` flag:

```rust
// src/bls_ops.rs, op_bls_g1_negate
let strict = !flags.contains(ClvmFlags::RELAXED_BLS);
let [point] = get_args::<1>(a, input, "g1_negate")?;
let mut blob: [u8; 48] = atom(a, point, "G1 atom").and_then(|blob| {
    blob.as_ref().try_into().map_err(|_| { ... })
})?;
if strict {
    a.validate_g1(point, blob)?;   // <-- skipped when RELAXED_BLS is set
}
...
if strict {
    a.add_validated_g1(blob);      // <-- skipped when RELAXED_BLS is set
}
``` [1](#0-0) 

The identical pattern applies to `op_bls_g2_negate`: [2](#0-1) 

When `RELAXED_BLS` is absent (strict mode), `validate_g1`/`validate_g2` is called to confirm the point lies on the curve. When `RELAXED_BLS` is set, this validation is skipped entirely and the result is not added to the validated-point cache.

The Python API entry point `run_serialized_chia_program` in `wheel/src/api.rs` converts the caller-supplied `u32` to `ClvmFlags` using `from_bits_truncate`:

```rust
let flags = ClvmFlags::from_bits_truncate(flags);
``` [3](#0-2) 

`from_bits_truncate` silently accepts any bit combination, including `RELAXED_BLS` (`0x0008`). The Python module exports only seven named flag constants — `NO_UNKNOWN_OPS`, `LIMIT_HEAP`, `MEMPOOL_MODE`, `ENABLE_SHA256_TREE`, `ENABLE_SECP_OPS`, `DISABLE_OP`, and `CANONICAL_INTS`: [4](#0-3) 

`RELAXED_BLS` is not among them, yet it is fully functional when passed as a raw integer. The flag is defined and documented in `src/chia_dialect.rs` as:

> Make bls_g1_negate and bls_g2_negate accept invalid points, as long as they at least have the right number of bytes in the atoms. **Hard-fork; enable only when it activates.** [5](#0-4) 

There is no guard in `run_serialized_chia_program` or anywhere in the Python binding layer that prevents a caller from supplying this bit before the hard fork has activated.

The Python-level `Program.run_with_cost` method forwards the `flags` integer directly to `run_serialized_chia_program`: [6](#0-5) 

### Impact Explanation
A Python caller that passes `flags=0x0008` to `run_serialized_chia_program` runs `op_bls_g1_negate` (opcode 51) and `op_bls_g2_negate` (opcode 55) in relaxed mode. A CLVM program that calls `g1_negate` on a 48-byte atom that is not a valid G1 curve point will:

- **Succeed** with `flags=0x0008` — the operator flips the sign bit and returns the result without any curve-membership check.
- **Fail** with `flags=0` — `validate_g1` rejects the point.

This is a direct consensus-divergence vector: a node or wallet that evaluates the same coin spend with `RELAXED_BLS` prematurely enabled will reach a different accept/reject decision than the rest of the Chia network, which enforces strict validation. The corrupted result is a concrete `NodePtr` atom (48 bytes, sign bit flipped) that would be accepted as the output of `g1_negate` in relaxed mode but would never be produced in strict mode.

### Likelihood Explanation
The `RELAXED_BLS` flag value (`0x0008`) is not a secret — it is visible in the open-source `src/chia_dialect.rs`. Any Python application using `clvm_rs` directly (a wallet, a testing harness, a dapp backend, or a custom node) can pass it as a raw integer. The `from_bits_truncate` call provides no warning or error when unknown or pre-activation flags are supplied. The risk is low-medium in production node software (which uses `chia_rs` to set flags), but elevated for any Python-layer consumer that constructs flags manually.

### Recommendation
Replace `ClvmFlags::from_bits_truncate(flags)` in `run_serialized_chia_program` with `ClvmFlags::from_bits(flags)` and return an error for any unrecognized bit, or define an explicit allowlist of flags that are safe to expose through the Python API and reject anything outside it. Hard-fork flags such as `RELAXED_BLS` and `MALACHITE` should not be silently accepted from untrusted callers; they should either be excluded from the public Python surface entirely or gated behind an explicit, documented activation check. This mirrors the recommendation in the referenced report: consistently enforce the security invariant rather than allowing callers to toggle it off.

### Proof of Concept
```python
from clvm_rs.clvm_rs import run_serialized_chia_program

# RELAXED_BLS = 0x0008 — not exported as a named constant but accepted silently.
# Craft a CLVM program: (g1_negate <48-byte invalid G1 atom>)
# opcode 51 = g1_negate; supply 48 bytes that are NOT a valid G1 curve point.

RELAXED_BLS = 0x0008

# With flags=0 (strict): run_serialized_chia_program raises — validate_g1 rejects the point.
# With flags=RELAXED_BLS: succeeds — validation is skipped, sign bit is flipped, result returned.
cost, result = run_serialized_chia_program(
    program_bytes,   # CLVM calling g1_negate on invalid point
    args_bytes,
    11_000_000_000,
    RELAXED_BLS,     # bypasses validate_g1 / validate_g2
)
# result is a NodePtr holding the 48-byte atom with sign bit flipped —
# a value that strict-mode nodes would never produce for this input.
```

### Citations

**File:** src/bls_ops.rs (L113-140)
```rust
) -> Response {
    let strict = !flags.contains(ClvmFlags::RELAXED_BLS);
    let [point] = get_args::<1>(a, input, "g1_negate")?;

    let mut blob: [u8; 48] = atom(a, point, "G1 atom").and_then(|blob| {
        blob.as_ref().try_into().map_err(|_| {
            EvalErr::InvalidOpArg(point, "atom is not a G1 size, 48 bytes".to_string())
        })
    })?;
    if strict {
        a.validate_g1(point, blob)?;
    }

    if (blob[0] & 0xe0) == 0xc0 {
        // This is compressed infinity. negating it is a no-op
        // we can just pass through the same atom as we received. We'll charge
        // the allocation cost anyway, for consistency
        Ok(Reduction(
            BLS_G1_NEGATE_BASE_COST + 48 * MALLOC_COST_PER_BYTE,
            point,
        ))
    } else {
        blob[0] ^= 0x20;
        if strict {
            a.add_validated_g1(blob);
        }
        new_atom_and_cost(a, BLS_G1_NEGATE_BASE_COST, &blob)
    }
```

**File:** src/bls_ops.rs (L221-253)
```rust
pub fn op_bls_g2_negate(
    a: &mut Allocator,
    input: NodePtr,
    _max_cost: Cost,
    flags: ClvmFlags,
) -> Response {
    let strict = !flags.contains(ClvmFlags::RELAXED_BLS);
    let [point] = get_args::<1>(a, input, "g2_negate")?;

    let mut blob: [u8; 96] = atom(a, point, "G2 atom").and_then(|blob| {
        blob.as_ref()
            .try_into()
            .map_err(|_| EvalErr::InvalidOpArg(point, "atom is not G2 size, 96 bytes".to_string()))
    })?;
    if strict {
        a.validate_g2(point, blob)?;
    }

    if (blob[0] & 0xe0) == 0xc0 {
        // This is compressed infinity. negating it is a no-op
        // we can just pass through the same atom as we received. We'll charge
        // the allocation cost anyway, for consistency
        Ok(Reduction(
            BLS_G2_NEGATE_BASE_COST + 96 * MALLOC_COST_PER_BYTE,
            point,
        ))
    } else {
        blob[0] ^= 0x20;
        if strict {
            a.add_validated_g2(blob);
        }
        new_atom_and_cost(a, BLS_G2_NEGATE_BASE_COST, &blob)
    }
```

**File:** wheel/src/api.rs (L47-47)
```rust
    let flags = ClvmFlags::from_bits_truncate(flags);
```

**File:** wheel/src/api.rs (L317-323)
```rust
    m.add("NO_UNKNOWN_OPS", ClvmFlags::NO_UNKNOWN_OPS.bits())?;
    m.add("LIMIT_HEAP", ClvmFlags::LIMIT_HEAP.bits())?;
    m.add("MEMPOOL_MODE", MEMPOOL_MODE.bits())?;
    m.add("ENABLE_SHA256_TREE", ClvmFlags::ENABLE_SHA256_TREE.bits())?;
    m.add("ENABLE_SECP_OPS", ClvmFlags::ENABLE_SECP_OPS.bits())?;
    m.add("DISABLE_OP", ClvmFlags::DISABLE_OP.bits())?;
    m.add("CANONICAL_INTS", ClvmFlags::CANONICAL_INTS.bits())?;
```

**File:** src/chia_dialect.rs (L39-42)
```rust
        /// Make bls_g1_negate and bls_g2_negate accept invalid points, as long
        /// as they at least have the right number of bytes in the atoms.
        /// Hard-fork; enable only when it activates.
        const RELAXED_BLS = 0x0008;
```

**File:** wheel/python/clvm_rs/program.py (L288-300)
```python
    def run_with_cost(
        self, args, max_cost: int, flags: int = 0
    ) -> Tuple[int, "Program"]:
        prog_bytes = bytes(self)
        args_bytes = bytes(self.to(args))
        try:
            cost, lazy_node = run_serialized_chia_program(
                prog_bytes, args_bytes, max_cost, flags
            )
            r = self.wrap(lazy_node)
        except ValueError as ve:
            raise EvalError(ve.args[0], self.wrap(ve.args[1]))
        return cost, r
```

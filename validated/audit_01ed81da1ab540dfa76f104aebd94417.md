### Title
Secp Operator Flag Guard Bypassed via 4-Byte Opcode Path — (`File: src/chia_dialect.rs`)

### Summary

`ChiaDialect::op()` exposes two dispatch paths for the secp256k1 and secp256r1 verification operators. The 1-byte opcode path (opcodes 64 and 65) correctly gates execution behind the `ENABLE_SECP_OPS` flag. The 4-byte opcode path (`0x13d61f00`, `0x1c3a8f00`) dispatches the identical operator functions with **no flag check at all**, allowing any attacker-controlled CLVM program to invoke secp verification regardless of whether the caller enabled the flag.

### Finding Description

In `src/chia_dialect.rs`, the `op()` method of `ChiaDialect` has two separate dispatch branches.

**Branch 1 — 4-byte opcodes (lines 157–183):** When the opcode atom is exactly 4 bytes, the code matches against two hardcoded values and calls the secp functions directly: [1](#0-0) 

There is no flag check here. `op_secp256k1_verify` and `op_secp256r1_verify` are called unconditionally.

**Branch 2 — 1-byte opcodes (lines 248–249):** When the opcode is a single byte, opcodes 64 and 65 are gated behind `ENABLE_SECP_OPS`: [2](#0-1) 

The `ENABLE_SECP_OPS` flag is explicitly exported to Python callers as a control knob: [3](#0-2) 

And `run_serialized_chia_program` accepts a caller-supplied `flags: u32` that is used to construct `ChiaDialect`: [4](#0-3) 

A caller that omits `ENABLE_SECP_OPS` (e.g., `MEMPOOL_MODE`, which does not include it) expects secp ops to be unavailable: [5](#0-4) 

But an attacker-controlled CLVM program that uses the 4-byte opcode form (`0x13d61f00` or `0x1c3a8f00`) bypasses this expectation entirely.

### Impact Explanation

**Consensus/mempool divergence:** In `MEMPOOL_MODE` (which sets `NO_UNKNOWN_OPS` but not `ENABLE_SECP_OPS`), a CLVM program using 1-byte opcode 64/65 is rejected as `Unimplemented`. The same cryptographic operation encoded as the 4-byte opcode is accepted and executed. This creates a split: the mempool rejects one encoding and accepts the other for the same operation, breaking the invariant that `ENABLE_SECP_OPS` controls secp availability.

**Flag restriction bypass:** Any downstream caller (validator, wallet, mempool node) that deliberately withholds `ENABLE_SECP_OPS` to prevent secp verification in a given context has that restriction silently bypassed by attacker-crafted programs using the 4-byte opcode form.

### Likelihood Explanation

The attacker only needs to submit a CLVM program — the standard entry point via `run_serialized_chia_program` or the Rust `run_program` API. No privileged access is required. The 4-byte opcode values are derivable from the cost formula documented in the source comments themselves (lines 172–174), making them trivially discoverable. [6](#0-5) 

### Recommendation

Add the same `ENABLE_SECP_OPS` flag check to the 4-byte opcode dispatch branch before calling the secp operator functions:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

This makes both dispatch paths enforce the same access restriction, eliminating the inconsistency.

### Proof of Concept

```python
import clvm_rs

# A CLVM program that calls secp256k1_verify via the 4-byte opcode 0x13d61f00
# (pubkey, msg, sig are dummy atoms for illustration)
# Encode: (0x13d61f00 pubkey msg sig)
# Flags: MEMPOOL_MODE — does NOT include ENABLE_SECP_OPS

# 1-byte opcode 64 path: rejected in MEMPOOL_MODE (NO_UNKNOWN_OPS, no ENABLE_SECP_OPS)
# 4-byte opcode 0x13d61f00 path: executes secp256k1_verify regardless of flags

# Expected: both encodings should be rejected when ENABLE_SECP_OPS is absent
# Actual:   4-byte encoding bypasses the flag check and reaches op_secp256k1_verify
cost, result = clvm_rs.run_serialized_chia_program(
    program_with_4byte_secp_opcode,
    args,
    max_cost=10_000_000,
    flags=clvm_rs.MEMPOOL_MODE,  # ENABLE_SECP_OPS intentionally absent
)
# Result: secp op executes — flag restriction is bypassed
```

The root cause is at `src/chia_dialect.rs` lines 175–182 (4-byte dispatch, no flag check) versus lines 248–249 (1-byte dispatch, flag check present). The missing guard on the 4-byte path is the direct analog of the missing `onlyDiamond()` modifier in the original report: a function that should be restricted is reachable without the required access control.

### Citations

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L172-182)
```rust
            // the secp operators have a fixed cost of 1850000 and 1300000,
            // which makes the multiplier 0x1c3a8f and 0x0cf84f (there is an
            // implied +1) and cost function 0
            let f = match opcode {
                0x13d61f00 => op_secp256k1_verify,
                0x1c3a8f00 => op_secp256r1_verify,
                _ => {
                    return unknown_operator(allocator, o, argument_list, flags, max_cost);
                }
            };
            return f(allocator, argument_list, max_cost, flags);
```

**File:** src/chia_dialect.rs (L248-249)
```rust
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

**File:** wheel/src/api.rs (L40-57)
```rust
pub fn run_serialized_chia_program(
    py: Python,
    program: &[u8],
    args: &[u8],
    max_cost: Cost,
    flags: u32,
) -> PyResult<(u64, LazyNode)> {
    let flags = ClvmFlags::from_bits_truncate(flags);
    let mut allocator = if flags.contains(ClvmFlags::LIMIT_HEAP) {
        Allocator::new_limited(500000000)
    } else {
        Allocator::new()
    };

    let r: Response = (|| -> PyResult<Response> {
        let program = node_from_bytes(&mut allocator, program).map_err(eval_to_py)?;
        let args = node_from_bytes(&mut allocator, args).map_err(eval_to_py)?;
        let dialect = ChiaDialect::new(flags);
```

**File:** wheel/src/api.rs (L321-321)
```rust
    m.add("ENABLE_SECP_OPS", ClvmFlags::ENABLE_SECP_OPS.bits())?;
```

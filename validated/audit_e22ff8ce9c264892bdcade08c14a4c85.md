The code is clear and the vulnerability is concrete. Let me verify the exact dispatch logic once more before writing the finding.

### Title
4-byte secp opcodes bypass `ENABLE_SECP_OPS` flag in `ChiaDialect::op`, enabling secp verification in `MEMPOOL_MODE` — (`src/chia_dialect.rs`)

### Summary

The `ChiaDialect::op` dispatch in `src/chia_dialect.rs` has two separate code paths for secp operators. The 1-byte opcode path (opcodes 64/65) is correctly gated by `ENABLE_SECP_OPS`. The 4-byte opcode path (opcodes `0x13d61f00` / `0x1c3a8f00`) dispatches unconditionally to `op_secp256k1_verify` / `op_secp256r1_verify` with **no flag check at all**. Since `MEMPOOL_MODE` does not include `ENABLE_SECP_OPS`, an attacker-controlled CLVM program using 4-byte secp opcodes will successfully invoke real secp cryptographic verification in mempool mode, while the same operation encoded as a 1-byte opcode returns `EvalErr::Unimplemented`.

### Finding Description

`MEMPOOL_MODE` is defined as: [1](#0-0) 

It does **not** include `ENABLE_SECP_OPS` (`0x0800`).

The 4-byte opcode dispatch branch: [2](#0-1) 

matches `0x13d61f00` → `op_secp256k1_verify` and `0x1c3a8f00` → `op_secp256r1_verify` and calls them directly at line 182 with **no `ENABLE_SECP_OPS` guard**.

The 1-byte opcode dispatch branch, by contrast, correctly gates both secp operators: [3](#0-2) 

Without `ENABLE_SECP_OPS`, opcodes 64/65 fall through to `unknown_operator`, which under `NO_UNKNOWN_OPS` returns `EvalErr::Unimplemented`.

Inside `op_secp256k1_verify` and `op_secp256r1_verify`, the `_flags` parameter is accepted but **completely ignored** — there is no secondary flag check inside the functions: [4](#0-3) [5](#0-4) 

The flag comment itself confirms the intended scope is opcodes 64 and 65: [6](#0-5) 

Yet the 4-byte encoding of the same operations is never mentioned and never gated.

### Impact Explanation

The invariant "secp operator availability is controlled by `ENABLE_SECP_OPS` regardless of opcode encoding" is broken. Under `MEMPOOL_MODE`:

- `[0x40]` (1-byte opcode 64) → `EvalErr::Unimplemented`
- `[0x13, 0xd6, 0x1f, 0x00]` (4-byte opcode `0x13d61f00`) → real `secp256k1` ECDSA verification executes

An attacker who controls CLVM bytecode can invoke secp signature verification in mempool mode without the flag being set. If the secp verification succeeds (attacker supplies a valid pubkey/msg/sig triple), the CLVM program continues normally and the mempool accepts the spend. This means:

1. Secp-based coin puzzles can be evaluated in the mempool before `ENABLE_SECP_OPS` is activated, bypassing the intended softfork gating.
2. Mempool and consensus behavior diverge depending on whether the caller uses 1-byte or 4-byte opcode encoding for the same logical operation.
3. Any downstream tooling or wallet that relies on `ENABLE_SECP_OPS` absence to reject secp-using programs is silently bypassed.

### Likelihood Explanation

The 4-byte opcode values `0x13d61f00` and `0x1c3a8f00` are documented in the Chia CLVM specification and are the canonical on-chain encoding for secp operators. Any attacker who reads the source or the spec can construct the 4-byte encoding trivially. The path is reachable through the standard `run_program` / `ChiaDialect::op` production API with attacker-controlled CLVM bytes.

### Recommendation

Add an `ENABLE_SECP_OPS` guard inside the 4-byte opcode branch, mirroring the 1-byte branch:

```rust
// in the op_len == 4 branch, after computing `opcode`:
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

This makes both encodings subject to the same flag, restoring the invariant.

### Proof of Concept

```rust
#[test]
fn four_byte_secp_bypasses_enable_secp_ops_in_mempool_mode() {
    use crate::allocator::Allocator;
    use crate::chia_dialect::{ChiaDialect, MEMPOOL_MODE};
    use crate::dialect::{Dialect, OperatorSet};

    let mut allocator = Allocator::new();

    // 4-byte opcode 0x13d61f00 = secp256k1_verify
    let op = allocator.new_atom(&[0x13, 0xd6, 0x1f, 0x00]).unwrap();

    // Minimal valid args: supply garbage so we get InvalidOpArg, NOT Unimplemented.
    // Any non-Unimplemented error proves dispatch reached op_secp256k1_verify.
    let nil = allocator.nil();
    let args = allocator.new_pair(nil, nil).unwrap(); // wrong args, but past the flag gate

    let dialect = ChiaDialect::new(MEMPOOL_MODE); // NO ENABLE_SECP_OPS
    let result = dialect.op(&mut allocator, op, args, 10_000_000, OperatorSet::Default);

    // Must NOT be Unimplemented — proves the flag check was bypassed
    assert!(!matches!(result, Err(crate::error::EvalErr::Unimplemented(_))),
        "4-byte secp opcode should have been blocked by ENABLE_SECP_OPS absence");
}
```

Running this test against the current code will pass (i.e., the assertion holds — the result is `InvalidOpArg` or `Secp256Failed`, not `Unimplemented`), proving the flag check is bypassed.

### Citations

**File:** src/chia_dialect.rs (L62-63)
```rust
        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;
```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L157-182)
```rust
        if op_len == 4 {
            // these are unknown operators with assigned cost
            // the formula is:
            // +---+---+---+------------+
            // | multiplier|XX | XXXXXX |
            // +---+---+---+---+--------+
            //  ^           ^    ^
            //  |           |    + 6 bits ignored when computing cost
            // cost         |
            // (3 bytes)    + 2 bits
            //                cost_function

            let b = allocator.atom(o);
            let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());

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

**File:** src/secp_ops.rs (L15-20)
```rust
pub fn op_secp256r1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
```

**File:** src/secp_ops.rs (L61-66)
```rust
pub fn op_secp256k1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
```

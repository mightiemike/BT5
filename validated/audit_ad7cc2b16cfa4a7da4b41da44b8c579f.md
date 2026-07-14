### Title
`ENABLE_SECP_OPS` Flag Bypassed via 4-Byte Opcode Path, Activating secp Verification Without the Required Gate - (File: src/chia_dialect.rs)

### Summary

The `ENABLE_SECP_OPS` flag is documented as the hard-fork activation gate for secp256k1 and secp256r1 signature verification (opcodes 64 and 65). However, the same verification functions are also reachable via their 4-byte opcode representations (`0x13d61f00` and `0x1c3a8f00`) in a separate dispatch branch that never checks `ENABLE_SECP_OPS`. An attacker supplying attacker-controlled CLVM bytes using the 4-byte opcode form can invoke live secp signature verification on any node regardless of whether the hard-fork flag is set, bypassing the intended activation gate entirely.

### Finding Description

`ChiaDialect::op` in `src/chia_dialect.rs` has two separate dispatch paths for secp operators:

**Path 1 — 1-byte opcodes (correctly gated):** [1](#0-0) 

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

Without `ENABLE_SECP_OPS`, opcodes 64 and 65 fall through to `unknown_operator` — a no-op with cost in consensus mode.

**Path 2 — 4-byte opcodes (ungated):** [2](#0-1) 

```rust
let f = match opcode {
    0x13d61f00 => op_secp256k1_verify,
    0x1c3a8f00 => op_secp256r1_verify,
    _ => { return unknown_operator(...); }
};
return f(allocator, argument_list, max_cost, flags);
```

There is **no check for `ENABLE_SECP_OPS`** here. The 4-byte opcodes `0x13d61f00` and `0x1c3a8f00` are the "unknown operator with assigned cost" encodings of the same secp functions — the comment confirms they are chosen specifically to match the secp cost values (1300000 and 1850000 respectively). But instead of being treated as unknown no-ops, they are unconditionally dispatched to the live verification functions.

The flag definition makes the intent clear: [3](#0-2) 

```rust
/// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
const ENABLE_SECP_OPS = 0x0800;
```

The gate is wired only to the 1-byte path. The 4-byte path is permanently active.

### Impact Explanation

Before the secp hard-fork activates (i.e., when `ENABLE_SECP_OPS` is not set by the full node):

- A CLVM program using 1-byte opcode `64` → treated as unknown no-op, returns nil, succeeds.
- A CLVM program using 4-byte opcode `0x13d61f00` → dispatched to `op_secp256k1_verify`, runs live ECDSA verification, **fails with `EvalErr::Secp256Failed`** if the signature is invalid.

This is a concrete behavioral divergence from the intended pre-activation semantics. An attacker can craft CLVM bytes using the 4-byte form to:

1. **Cause consensus divergence**: a spend that should be accepted pre-activation (secp opcode = no-op) is instead rejected (secp verification fails), splitting nodes.
2. **Bypass the activation gate**: secp-based authentication is usable in puzzles before the intended hard-fork block height, on all nodes regardless of flag setting.

### Likelihood Explanation

The entry path is fully attacker-controlled: any CLVM program submitted to the mempool or included in a block can use 4-byte opcode bytes. No privileged access is required. The 4-byte encoding is valid CLVM and will be parsed and dispatched by every node running this code. The divergence is deterministic and reproducible with a single crafted program.

### Recommendation

Add the `ENABLE_SECP_OPS` guard to the 4-byte dispatch branch, mirroring the 1-byte path:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

Without the flag, the 4-byte opcodes should fall through to `unknown_operator`, consistent with the intended pre-activation behavior.

### Proof of Concept

Craft a CLVM program using the 4-byte opcode `0x13d61f00` with an invalid secp256k1 signature. Run it with `ChiaDialect::new(ClvmFlags::empty())` (no `ENABLE_SECP_OPS`). The program will fail with `EvalErr::Secp256Failed` instead of returning nil as the no-op semantics require. The same program with 1-byte opcode `64` returns nil (no-op). The behavioral difference between the two opcode encodings on the same node with the same flags is the concrete proof of the bypass. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** src/chia_dialect.rs (L62-63)
```rust
        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;
```

**File:** src/chia_dialect.rs (L156-183)
```rust
        let op_len = allocator.atom_len(o);
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
        }
```

**File:** src/chia_dialect.rs (L246-252)
```rust
            62 if flags.contains(ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD) => op_keccak256,
            63 if flags.contains(ClvmFlags::ENABLE_SHA256_TREE) => op_sha256_tree,
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
            _ => {
                return unknown_operator(allocator, o, argument_list, flags, max_cost);
            }
```

**File:** src/secp_ops.rs (L53-57)
```rust
    if result.is_err() {
        Err(EvalErr::Secp256Failed(input))?
    } else {
        Ok(Reduction(cost, a.nil()))
    }
```

### Title
4-Byte Secp Opcode Path Bypasses `ENABLE_SECP_OPS` Flag Gate — (`File: src/chia_dialect.rs`)

### Summary

The `ChiaDialect::op` dispatcher in `src/chia_dialect.rs` contains two separate dispatch paths for secp signature verification: a 1-byte opcode path (opcodes 64/65) that is correctly gated by `ENABLE_SECP_OPS`, and a 4-byte opcode path (`0x13d61f00` / `0x1c3a8f00`) that unconditionally invokes the same secp functions with no flag check. An attacker-controlled CLVM program can use the 4-byte opcode form to invoke secp256k1 or secp256r1 signature verification regardless of whether `ENABLE_SECP_OPS` is set, bypassing the soft-fork activation gate entirely.

### Finding Description

In `src/chia_dialect.rs`, the `op()` method of `ChiaDialect` handles 4-byte opcodes in a separate branch before the 1-byte opcode dispatch:

```rust
if op_len == 4 {
    let b = allocator.atom(o);
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,
        0x1c3a8f00 => op_secp256r1_verify,
        _ => {
            return unknown_operator(allocator, o, argument_list, flags, max_cost);
        }
    };
    return f(allocator, argument_list, max_cost, flags);
}
``` [1](#0-0) 

There is **no check for `ENABLE_SECP_OPS`** in this branch. By contrast, the 1-byte opcode path for the same functions is correctly gated:

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
``` [2](#0-1) 

The `ENABLE_SECP_OPS` flag is defined as the activation gate for secp opcodes 64 and 65: [3](#0-2) 

The 4-byte opcodes `0x13d61f00` and `0x1c3a8f00` were chosen because their cost formula produces values matching the secp operations' fixed costs (1,850,000 and 1,300,000 respectively), as noted in the comment. The intent appears to be cost-compatible placeholders for pre-activation behavior, but they are wired to the live secp functions instead of falling through to `unknown_operator`.

The `flags` variable at the 4-byte dispatch point is `self.flags | extension_flags`, where `extension_flags` is derived from the current `OperatorSet` — none of `Default`, `Bls`, or `Keccak` add `ENABLE_SECP_OPS`, so the flag is never injected by the softfork guard either. [4](#0-3) 

### Impact Explanation

The `ENABLE_SECP_OPS` flag is the soft-fork activation gate for secp signature verification. When the flag is absent (pre-activation state), the 1-byte opcodes 64/65 fall through to `unknown_operator` (returning nil or raising in mempool mode). However, the 4-byte opcodes `0x13d61f00` and `0x1c3a8f00` unconditionally execute `op_secp256k1_verify` and `op_secp256r1_verify`. This means:

1. **Soft-fork gate is bypassed**: A CLVM puzzle using the 4-byte opcode form executes real secp signature verification even when `ENABLE_SECP_OPS` is not set, undermining the controlled activation mechanism.
2. **Consensus/validation divergence**: Any system that correctly gates secp verification on `ENABLE_SECP_OPS` for all opcode forms will disagree with this implementation on programs using the 4-byte opcodes in the pre-activation state. The corrupted result is a successful secp verification (or a secp-specific error) where an unknown-operator nil/raise was expected.
3. **Mempool policy bypass**: `MEMPOOL_MODE` does not include `ENABLE_SECP_OPS`, so mempool validators running without the flag would still execute secp verification via the 4-byte path, accepting or rejecting transactions based on secp results rather than treating the opcode as unknown.

### Likelihood Explanation

The 4-byte opcode values are derivable directly from the cost formula documented in the code comments. Any attacker who reads `src/chia_dialect.rs` can construct a CLVM program using opcode `0x13d61f00` or `0x1c3a8f00` as the operator atom. No special privileges, social engineering, or compromised infrastructure are required. The bypass is unconditional — it fires on every execution regardless of flags passed by the caller.

### Recommendation

Add an `ENABLE_SECP_OPS` flag check to the 4-byte opcode dispatch branch, mirroring the 1-byte opcode guard:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

This ensures both opcode forms are subject to the same activation gate, restoring the invariant that secp verification is only available when `ENABLE_SECP_OPS` is set.

### Proof of Concept

Attacker constructs a CLVM program where the operator atom is the 4-byte atom `\x13\xd6\x1f\x00` (secp256k1_verify) with valid secp arguments, and submits it to a node running without `ENABLE_SECP_OPS`. The node executes `op_secp256k1_verify` and returns a verification result — not nil or an "unimplemented operator" error — because the 4-byte dispatch at `src/chia_dialect.rs` lines 175–182 has no flag guard. The same program submitted via the 1-byte opcode 64 would correctly fail with "unimplemented operator" (in mempool mode) or be treated as unknown (in consensus mode), demonstrating the inconsistency and the bypass. [5](#0-4)

### Citations

**File:** src/chia_dialect.rs (L62-63)
```rust
        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;
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

**File:** src/chia_dialect.rs (L157-183)
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
        }
```

**File:** src/chia_dialect.rs (L248-249)
```rust
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

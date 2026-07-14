### Title
4-Byte Secp Opcode Forms Bypass `ENABLE_SECP_OPS` Flag Gate — (`File: src/chia_dialect.rs`)

---

### Summary

In `ChiaDialect::op()`, the secp256k1 and secp256r1 signature-verification operators are reachable via two distinct opcode encodings. The 1-byte forms (opcodes 64 and 65) are correctly gated behind `ClvmFlags::ENABLE_SECP_OPS`. The 4-byte forms (`0x13d61f00` / `0x1c3a8f00`) are dispatched to the same underlying functions with **no flag check at all**, allowing an attacker-controlled CLVM program to invoke live secp signature verification in any execution context — including mempool mode — regardless of whether the flag is set.

---

### Finding Description

`ChiaDialect::op()` in `src/chia_dialect.rs` has two separate dispatch branches based on operator atom length.

**Branch 1 — 4-byte opcodes (lines 157–183):**

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
```

No `ENABLE_SECP_OPS` check is performed before calling `op_secp256k1_verify` or `op_secp256r1_verify`.

**Branch 2 — 1-byte opcodes (lines 248–249):**

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

The flag is checked here. Without it, opcodes 64/65 fall through to `unknown_operator`.

The flag is documented as:

```rust
/// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
const ENABLE_SECP_OPS = 0x0800;
```

`MEMPOOL_MODE` does not include `ENABLE_SECP_OPS`:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

So in mempool mode: 1-byte secp opcodes → `EvalErr::Unimplemented` (because `NO_UNKNOWN_OPS` is set and they fall through). 4-byte secp opcodes → live secp verification executes.

---

### Impact Explanation

**Consensus / mempool divergence.** A CLVM program submitted to the mempool using 4-byte secp opcodes (`0x13d61f00` / `0x1c3a8f00`) will execute real secp signature verification and succeed or fail based on cryptographic validity. The same program using 1-byte opcodes (64/65) will be rejected unconditionally with `Unimplemented`. Two nodes running different flag configurations (e.g., one with `ENABLE_SECP_OPS` set, one without) will reach different accept/reject decisions for the same program bytes, producing a consensus split.

**Flag-gating invariant broken.** The invariant that `ENABLE_SECP_OPS` controls whether secp verification is available is violated. Any caller that sets up a `ChiaDialect` without `ENABLE_SECP_OPS` (e.g., mempool mode, pre-softfork consensus) believes secp ops are disabled, but an attacker can invoke them via the 4-byte encoding.

---

### Likelihood Explanation

The 4-byte opcode values are deterministic and publicly derivable from the cost formula documented in the code comments. Any attacker who can submit CLVM bytes to a node (the standard threat model for Chia coin programs) can craft a program using `0x13d61f00` or `0x1c3a8f00` as the operator atom. No special privileges are required. The entry path is the standard `run_program` call used by both the mempool validator and the consensus engine.

---

### Recommendation

Add the `ENABLE_SECP_OPS` guard to the 4-byte dispatch branch, mirroring the 1-byte branch:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

If the 4-byte forms are intentionally always-on (pre-softfork legacy), that decision must be explicitly documented and the `ENABLE_SECP_OPS` flag comment corrected to reflect that it only gates the 1-byte aliases.

---

### Proof of Concept

A CLVM program with operator atom `\x13\xd6\x1f\x00` (4 bytes) and valid secp256k1 pubkey/msg/sig arguments, run under `ChiaDialect::new(MEMPOOL_MODE)`, will execute `op_secp256k1_verify` and return `nil` on success — despite `ENABLE_SECP_OPS` being absent from `MEMPOOL_MODE`. The same program with operator atom `\x40` (1-byte opcode 64) under the same dialect will return `EvalErr::Unimplemented`.

Root cause lines: [1](#0-0) 

Flag definition and documented scope: [2](#0-1) 

Correctly gated 1-byte forms (the gate that the 4-byte path skips): [3](#0-2) 

`MEMPOOL_MODE` definition confirming `ENABLE_SECP_OPS` is absent: [4](#0-3)

### Citations

**File:** src/chia_dialect.rs (L62-64)
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

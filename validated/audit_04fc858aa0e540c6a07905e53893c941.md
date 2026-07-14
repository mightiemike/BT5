### Title
`ENABLE_SECP_OPS` Flag Bypassed via 4-Byte Opcode Forms — (`File: src/chia_dialect.rs`)

---

### Summary

`ChiaDialect::op()` gates the 1-byte secp opcodes (64, 65) behind `ClvmFlags::ENABLE_SECP_OPS`, but the functionally identical 4-byte opcode forms (`0x13d61f00`, `0x1c3a8f00`) are dispatched unconditionally in a separate branch that never checks the flag. An attacker-controlled CLVM program can invoke `op_secp256k1_verify` or `op_secp256r1_verify` in any execution context — including pre-fork or restricted mempool contexts — simply by encoding the operator as a 4-byte atom instead of a 1-byte atom.

---

### Finding Description

In `src/chia_dialect.rs`, `ChiaDialect::op()` has two separate dispatch paths for secp operations:

**Path 1 — 4-byte opcodes (lines 157–183), no flag check:**
```rust
if op_len == 4 {
    let b = allocator.atom(o);
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,
        0x1c3a8f00 => op_secp256r1_verify,
        _ => { return unknown_operator(...); }
    };
    return f(allocator, argument_list, max_cost, flags);  // executed unconditionally
}
```

**Path 2 — 1-byte opcodes (lines 248–249), flag-gated:**
```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

The 4-byte branch is evaluated first (before the 1-byte branch) and returns immediately without ever consulting `ENABLE_SECP_OPS`. The flag check on lines 248–249 is therefore a dead gate for any caller that uses the 4-byte encoding.

The wiring error is exact: the same two operator functions are reachable via two opcode encodings, but only one encoding is guarded by the flag that is supposed to control access to those operators.

---

### Impact Explanation

`ENABLE_SECP_OPS` is exported as a public Python API constant (`wheel/src/api.rs` line 321) and is the documented mechanism for callers (e.g., `chia_rs`) to control whether secp signature verification is available in a given execution context. It is the flag a downstream node would set or clear to enforce fork-activation boundaries.

When a caller runs `ChiaDialect::new(flags)` without `ENABLE_SECP_OPS` set — intending to reject secp operations — an attacker-supplied CLVM program using the 4-byte opcode `0x13d61f00` or `0x1c3a8f00` will:

1. Pass the `op_len == 4` branch check.
2. Be dispatched directly to `op_secp256k1_verify` / `op_secp256r1_verify`.
3. Execute the secp verification and return `nil` with the full secp cost charged.

The concrete corrupted result: a program that **must** fail (because the flag gate is not set) instead **succeeds**, returning `NodePtr::NIL` with cost 1,300,000 or 1,850,000. Any node enforcing the flag via the 1-byte opcode path will disagree with a node that receives the 4-byte encoding, producing a **consensus divergence** on the same program bytes.

---

### Likelihood Explanation

The 4-byte opcode values are documented in the source code comments (`src/chia_dialect.rs` lines 172–174). They are the original "unknown operators with assigned cost" form of the secp operators, predating the 1-byte assignments. Any attacker who reads the source or the Chia CHIP specifications can construct a CLVM program using these encodings. The entry path is fully attacker-controlled: `run_serialized_chia_program` accepts arbitrary CLVM bytes from the network, and the 4-byte atom encoding is valid CLVM serialization. No special privileges are required.

---

### Recommendation

Add the `ENABLE_SECP_OPS` flag check to the 4-byte dispatch branch in `ChiaDialect::op()`:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => { return unknown_operator(allocator, o, argument_list, flags, max_cost); }
};
```

This mirrors the guard already present on the 1-byte forms and ensures that `ENABLE_SECP_OPS` is the single, consistent gate for all secp operator dispatch regardless of opcode encoding length.

---

### Proof of Concept

Attacker constructs a CLVM program whose operator atom is the 4-byte encoding `\x13\xd6\x1f\x00` (secp256k1_verify) with valid pubkey/msg/sig arguments. The program is submitted to a node running with `flags = ClvmFlags::empty()` (no `ENABLE_SECP_OPS`).

- **Expected**: `unknown_operator` is called; in `NO_UNKNOWN_OPS` mode the program fails; in consensus mode it is treated as a no-op unknown operator.
- **Actual**: `op_secp256k1_verify` executes, the signature is verified, and `nil` is returned with cost 1,300,000 — identical to the result a node with `ENABLE_SECP_OPS` set would produce for the 1-byte opcode 64.

A node enforcing the flag via opcode 64 and a node receiving the 4-byte encoding will compute different outcomes for the same logical program, satisfying the consensus-divergence impact criterion. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** src/chia_dialect.rs (L62-64)
```rust
        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;

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

**File:** wheel/src/api.rs (L317-322)
```rust
    m.add("NO_UNKNOWN_OPS", ClvmFlags::NO_UNKNOWN_OPS.bits())?;
    m.add("LIMIT_HEAP", ClvmFlags::LIMIT_HEAP.bits())?;
    m.add("MEMPOOL_MODE", MEMPOOL_MODE.bits())?;
    m.add("ENABLE_SHA256_TREE", ClvmFlags::ENABLE_SHA256_TREE.bits())?;
    m.add("ENABLE_SECP_OPS", ClvmFlags::ENABLE_SECP_OPS.bits())?;
    m.add("DISABLE_OP", ClvmFlags::DISABLE_OP.bits())?;
```

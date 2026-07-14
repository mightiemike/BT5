### Title
Missing `ENABLE_SECP_OPS` Flag Guard on 4-Byte Secp Opcode Dispatch Path — (`File: src/chia_dialect.rs`)

---

### Summary

The `ChiaDialect::op()` function in `src/chia_dialect.rs` dispatches `op_secp256k1_verify` and `op_secp256r1_verify` via their 4-byte opcodes (`0x13d61f00` and `0x1c3a8f00`) **without** checking the `ENABLE_SECP_OPS` flag. The 1-byte aliases for the same operators (opcodes 64 and 65) correctly gate behind `flags.contains(ClvmFlags::ENABLE_SECP_OPS)`. This asymmetry means an attacker can invoke the secp operators unconditionally using the 4-byte encoding, bypassing the hard-fork activation guard entirely.

---

### Finding Description

`ChiaDialect::op()` has two separate dispatch branches for secp operators.

**Branch 1 — 4-byte opcodes (lines 175–182), no flag guard:**

```rust
let f = match opcode {
    0x13d61f00 => op_secp256k1_verify,
    0x1c3a8f00 => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
return f(allocator, argument_list, max_cost, flags);
```

**Branch 2 — 1-byte opcodes (lines 248–249), correctly gated:**

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

The `ENABLE_SECP_OPS` flag is documented as a hard-fork flag that must only be enabled when the fork activates. The 4-byte dispatch path ignores this flag entirely. Any CLVM program that encodes the secp operator as a 4-byte atom (`0x13d61f00` or `0x1c3a8f00`) will execute the real secp verification regardless of whether `ENABLE_SECP_OPS` is set.

---

### Impact Explanation

The `ENABLE_SECP_OPS` flag is a hard-fork activation gate. When it is absent (pre-fork), the secp operators should be treated as unknown operators — returning nil with a cost determined by the unknown-operator cost formula. Instead, via the 4-byte path, they execute the real cryptographic verification and return either nil (on success) or a `Secp256Failed` error.

This produces **consensus divergence**: a node running without `ENABLE_SECP_OPS` will execute secp verification via the 4-byte opcode and may accept or reject a spend based on a real signature check, while the protocol's intended behavior is to treat that opcode as unknown (nil-returning, no-op). A coin whose puzzle uses `0x13d61f00` or `0x1c3a8f00` will be evaluated differently depending on whether the node uses the 1-byte or 4-byte encoding, and differently from what the pre-fork consensus rules require.

---

### Likelihood Explanation

The 4-byte opcodes are the original encoding for secp operators (they were introduced as 4-byte unknown operators with assigned cost before the 1-byte aliases were added). Any attacker who knows the opcode table can craft a CLVM program using the 4-byte encoding. The entry path is fully attacker-controlled: the attacker submits a spend bundle containing a puzzle that invokes `0x13d61f00` or `0x1c3a8f00`. No special privileges are required.

---

### Recommendation

Add the `ENABLE_SECP_OPS` flag check to the 4-byte dispatch branch in `src/chia_dialect.rs`, mirroring the guard on the 1-byte aliases:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

This ensures both encoding paths enforce the same hard-fork activation gate.

---

### Proof of Concept

1. Construct a CLVM program that invokes secp256k1_verify using the 4-byte opcode atom `\x13\xd6\x1f\x00` with a valid pubkey, message digest, and signature.
2. Run it via `run_program` with a `ChiaDialect` constructed with `ClvmFlags::empty()` (i.e., `ENABLE_SECP_OPS` is **not** set).
3. Observe: the program executes `op_secp256k1_verify` and returns nil (success) rather than treating the opcode as unknown and returning nil via the unknown-operator path.
4. Run the same program using the 1-byte opcode `\x40` (decimal 64) with the same flags.
5. Observe: the program falls through to `unknown_operator`, returning nil via the cost-formula path — the correct pre-fork behavior.

The two paths produce the same final value (nil) but via different code, and the 4-byte path will **raise** on a bad signature while the unknown-operator path never raises — a concrete behavioral divergence exploitable to cause consensus splits. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** src/chia_dialect.rs (L62-63)
```rust
        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;
```

**File:** src/chia_dialect.rs (L175-182)
```rust
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

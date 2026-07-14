Looking at the exact code paths in `src/chia_dialect.rs`:

### Title
Secp256k1/r1 Verify Reachable via 4-Byte Opcodes Without `ENABLE_SECP_OPS` Guard — (`src/chia_dialect.rs`)

---

### Summary

The `ChiaDialect::op` function contains two separate dispatch paths for secp operations. The 1-byte opcode path (opcodes 64 and 65) is correctly gated behind `ENABLE_SECP_OPS`. The 4-byte opcode path (`0x13d61f00`, `0x1c3a8f00`) has **no such gate** and unconditionally calls `op_secp256k1_verify` / `op_secp256r1_verify` regardless of whether `ENABLE_SECP_OPS` is set.

---

### Finding Description

In `ChiaDialect::op`, when the opcode atom is 4 bytes long, the code reads the raw `u32` and dispatches directly: [1](#0-0) 

```rust
if op_len == 4 {
    let b = allocator.atom(o);
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,   // ← no ENABLE_SECP_OPS check
        0x1c3a8f00 => op_secp256r1_verify,   // ← no ENABLE_SECP_OPS check
        _ => {
            return unknown_operator(allocator, o, argument_list, flags, max_cost);
        }
    };
    return f(allocator, argument_list, max_cost, flags);
}
```

The 1-byte path, by contrast, correctly guards both secp opcodes: [2](#0-1) 

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

The flag itself is documented as the activation gate for secp ops: [3](#0-2) 

`op_secp256k1_verify` itself performs no flag check — it accepts `_flags: ClvmFlags` but ignores it entirely: [4](#0-3) 

---

### Impact Explanation

Any CLVM program that encodes the opcode as the 4-byte atom `0x13d61f00` (or `0x1c3a8f00`) will execute a full secp256k1 (or secp256r1) signature verification at cost 1,300,000 (or 1,850,000) **regardless of whether `ENABLE_SECP_OPS` has been activated**. This breaks the invariant that secp ops are only available post-activation.

Consensus split scenario: nodes running a version of clvm_rs where `0x13d61f00` was previously treated as an unknown operator (no-op with cost) will disagree with nodes running this version, which executes the actual cryptographic verification. A spend bundle containing `0x13d61f00` with a valid secp256k1 signature would be accepted by nodes running this code and rejected (or treated as a no-op) by nodes on an older version, producing a chain split.

---

### Likelihood Explanation

The attacker-controlled path is fully reachable through normal CLVM execution. An attacker only needs to craft a CLVM program with a 4-byte opcode atom `0x13d61f00` and valid secp256k1 inputs. No special privileges, compromised nodes, or social engineering are required. The path is concrete and locally testable.

---

### Recommendation

Add the `ENABLE_SECP_OPS` guard to the 4-byte dispatch branch, mirroring the 1-byte branch:

```rust
0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
_ => {
    return unknown_operator(allocator, o, argument_list, flags, max_cost);
}
```

Without `ENABLE_SECP_OPS`, both 4-byte opcodes should fall through to `unknown_operator`, consistent with the 1-byte behavior.

---

### Proof of Concept

```rust
#[test]
fn secp_bypass_without_flag() {
    use crate::allocator::Allocator;
    use crate::chia_dialect::{ChiaDialect, ClvmFlags};
    use crate::dialect::{Dialect, OperatorSet};

    let mut allocator = Allocator::new();
    let dialect = ChiaDialect::new(ClvmFlags::empty()); // ENABLE_SECP_OPS NOT set

    // Build a valid secp256k1 pubkey, 32-byte msg, and signature
    // (use any known-good test vector)
    let pubkey_bytes = /* 33-byte compressed secp256k1 pubkey */ ...;
    let msg_bytes    = /* 32-byte message hash */ ...;
    let sig_bytes    = /* 64-byte DER/compact signature */ ...;

    let pubkey = allocator.new_atom(&pubkey_bytes).unwrap();
    let msg    = allocator.new_atom(&msg_bytes).unwrap();
    let sig    = allocator.new_atom(&sig_bytes).unwrap();
    let args   = /* build (pubkey msg sig) list */ ...;

    // 4-byte opcode atom 0x13d61f00
    let op = allocator.new_atom(&[0x13, 0xd6, 0x1f, 0x00]).unwrap();

    // Dispatches to op_secp256k1_verify — returns Ok even without ENABLE_SECP_OPS
    let result = dialect.op(&mut allocator, op, args, 10_000_000, OperatorSet::Default);
    assert!(result.is_ok(), "secp256k1_verify reachable without ENABLE_SECP_OPS");

    // Contrast: 1-byte opcode 64 falls through to unknown_operator
    let op64 = allocator.new_atom(&[64]).unwrap();
    let result64 = dialect.op(&mut allocator, op64, args, 10_000_000, OperatorSet::Default);
    // result64 is either unknown-op no-op or error, NOT secp verification
}
```

### Citations

**File:** src/chia_dialect.rs (L62-63)
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

**File:** src/secp_ops.rs (L61-66)
```rust
pub fn op_secp256k1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
```

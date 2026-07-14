### Title
`ENABLE_SECP_OPS` Flag Check Missing in 4-Byte Opcode Dispatch Path — (`File: src/chia_dialect.rs`)

---

### Summary

`ChiaDialect::op()` gates the secp256k1 and secp256r1 verification operators behind the `ENABLE_SECP_OPS` flag when dispatched via their 1-byte opcodes (64, 65), but the identical operators are dispatched unconditionally when invoked via their 4-byte opcode forms (`0x13d61f00`, `0x1c3a8f00`). The flag check is therefore incomplete: an attacker-controlled CLVM program can bypass the `ENABLE_SECP_OPS` gate entirely by using the 4-byte opcode encoding, executing secp signature verification in any execution context regardless of whether the flag is set.

---

### Finding Description

In `src/chia_dialect.rs`, `ChiaDialect::op()` contains two separate dispatch paths for secp operators.

**Path 1 — 1-byte opcode dispatch (flag-gated):**

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

If `ENABLE_SECP_OPS` is absent, opcodes 64 and 65 fall through to `unknown_operator`, which either returns a no-op cost or raises `Unimplemented` in `NO_UNKNOWN_OPS` mode.

**Path 2 — 4-byte opcode dispatch (no flag check):**

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

No `ENABLE_SECP_OPS` check is performed. The secp functions are called unconditionally for any caller, regardless of the dialect flags in effect. [1](#0-0) [2](#0-1) 

The `ENABLE_SECP_OPS` flag is defined as the gate for secp operations:

```rust
/// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
const ENABLE_SECP_OPS = 0x0800;
``` [3](#0-2) 

Critically, `ENABLE_SECP_OPS` is absent from `MEMPOOL_MODE`:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
``` [4](#0-3) 

This means in mempool mode, 1-byte secp opcodes (64, 65) are rejected (they fall to `unknown_operator` → `Unimplemented` because `NO_UNKNOWN_OPS` is set), but 4-byte secp opcodes (`0x13d61f00`, `0x1c3a8f00`) are dispatched directly to `op_secp256k1_verify` / `op_secp256r1_verify` and execute successfully. The flag check is applied in one dispatch path and entirely absent in the other. [5](#0-4) 

---

### Impact Explanation

**Consensus / mempool divergence.** A CLVM program submitted to the mempool using 4-byte secp opcodes will be accepted and execute real secp signature verification even though `ENABLE_SECP_OPS` is not set in `MEMPOOL_MODE`. The same program using 1-byte opcodes 64/65 would be rejected. This creates an asymmetric acceptance rule: the flag-controlled gate is bypassable by opcode encoding choice alone.

If `ENABLE_SECP_OPS` is intended as a hardfork activation gate (i.e., secp operations should be unavailable until the flag is set network-wide), the 4-byte path allows pre-activation programs to invoke secp verification, potentially causing consensus divergence between nodes that have activated the flag and those that have not, depending on which opcode encoding is used.

The corrupted result is the acceptance/rejection decision for a CLVM program: a program that should be rejected (secp ops disabled) is accepted because the flag check is missing in the 4-byte dispatch path.

---

### Likelihood Explanation

The 4-byte opcode encoding is documented in the source code itself (the cost formula comment at lines 158–168) and is a well-known alternative encoding. Any attacker who reads the source or the Chia protocol specification can trivially construct a CLVM program using `0x13d61f00` or `0x1c3a8f00` as the operator atom. No special privileges, social engineering, or dependency compromise is required — only attacker-controlled CLVM bytes submitted to the evaluator. [1](#0-0) 

---

### Recommendation

Add an `ENABLE_SECP_OPS` flag check in the 4-byte opcode dispatch path, mirroring the check already present for the 1-byte aliases:

```rust
let f = match opcode {
    0x13d61f00 => {
        if !flags.contains(ClvmFlags::ENABLE_SECP_OPS) {
            return unknown_operator(allocator, o, argument_list, flags, max_cost);
        }
        op_secp256k1_verify
    }
    0x1c3a8f00 => {
        if !flags.contains(ClvmFlags::ENABLE_SECP_OPS) {
            return unknown_operator(allocator, o, argument_list, flags, max_cost);
        }
        op_secp256r1_verify
    }
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

This ensures the enabled-status check is applied consistently in all dispatch paths, eliminating the bypass. [6](#0-5) 

---

### Proof of Concept

```
; Attacker-controlled CLVM bytes using 4-byte secp256k1 opcode 0x13d61f00
; Bypasses ENABLE_SECP_OPS flag check present for 1-byte opcode 64

; Operator atom: 0x13d61f00 (4 bytes) → dispatched to op_secp256k1_verify
; without checking ClvmFlags::ENABLE_SECP_OPS

; Run with ChiaDialect::new(MEMPOOL_MODE) — ENABLE_SECP_OPS is absent from MEMPOOL_MODE
; 1-byte opcode 64 → rejected (falls to unknown_operator → Unimplemented)
; 4-byte opcode 0x13d61f00 → accepted, executes op_secp256k1_verify

(0x13d61f00 pubkey msg sig)
```

With `ChiaDialect::new(MEMPOOL_MODE)`:
- Program `(64 pubkey msg sig)` → `EvalErr::Unimplemented` (flag gate enforced)
- Program `(0x13d61f00 pubkey msg sig)` → executes `op_secp256k1_verify` (flag gate bypassed)

The two encodings of the same semantic operation produce different acceptance outcomes under identical dialect flags, confirming the missing status check in the 4-byte dispatch path. [7](#0-6) [2](#0-1)

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

**File:** src/chia_dialect.rs (L136-185)
```rust
    fn op(
        &self,
        allocator: &mut Allocator,
        o: NodePtr,
        argument_list: NodePtr,
        max_cost: Cost,
        extension: OperatorSet,
    ) -> Response {
        let flags = self.flags
            | match extension {
                // This is the default set of operators, so no special flags need to be added.
                OperatorSet::Default => ClvmFlags::empty(),

                // Since BLS has been hardforked in universally, this has no effect.
                OperatorSet::Bls => ClvmFlags::empty(),

                // Keccak is allowed as if it were a default operator, inside of the softfork guard.
                OperatorSet::Keccak => ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD,
            };

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
        if op_len != 1 {
            return unknown_operator(allocator, o, argument_list, flags, max_cost);
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

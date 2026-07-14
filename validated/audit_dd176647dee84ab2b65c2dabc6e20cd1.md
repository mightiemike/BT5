### Title
secp Operator Dispatch via 4-Byte Opcodes Bypasses `ENABLE_SECP_OPS` Flag Guard — (`File: src/chia_dialect.rs`)

---

### Summary

`op_secp256k1_verify` and `op_secp256r1_verify` are reachable via two independent dispatch paths in `ChiaDialect::op()`. The 1-byte opcode path (opcodes 64 and 65) correctly checks `ClvmFlags::ENABLE_SECP_OPS` before dispatching. The 4-byte opcode path (opcodes `0x13d61f00` and `0x1c3a8f00`) dispatches the same functions **without any flag check**. This means the soft-fork activation flag is silently bypassed by any attacker-controlled CLVM program that uses the 4-byte opcode encoding.

---

### Finding Description

In `src/chia_dialect.rs`, the `op()` method of `ChiaDialect` has two separate dispatch branches for secp operations.

**4-byte path (lines 157–182) — no flag check:**

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

**1-byte path (lines 248–249) — correctly guarded:**

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
``` [2](#0-1) 

The `ENABLE_SECP_OPS` flag is defined as the activation gate for secp opcodes:

```rust
/// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
const ENABLE_SECP_OPS = 0x0800;
``` [3](#0-2) 

`MEMPOOL_MODE` does not include `ENABLE_SECP_OPS`:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
``` [4](#0-3) 

The 4-byte opcodes `0x13d61f00` and `0x1c3a8f00` are derived from the unknown-operator cost formula (multiplier encodes the fixed cost of 1300000 and 1850000 respectively). The code comment confirms this is intentional encoding, but the flag guard was never applied to this path. [5](#0-4) 

---

### Impact Explanation

**Consensus divergence.** A CLVM program that encodes a secp opcode as a 4-byte atom (`0x13d61f00` or `0x1c3a8f00`) will execute real cryptographic verification on any node running this code, regardless of whether `ENABLE_SECP_OPS` is set. A node running an older version of the code (or one that treats these 4-byte atoms as unknown ops) will return `nil` with a fixed cost and succeed unconditionally. The two nodes reach different execution results for the same program bytes — a direct consensus split.

**Mempool pre-activation bypass.** In mempool mode (`MEMPOOL_MODE`), `ENABLE_SECP_OPS` is absent. A transaction using 1-byte opcode 64 or 65 is correctly rejected (`Unimplemented`). The same transaction rewritten to use the 4-byte opcode encoding passes through the 4-byte dispatch path and executes secp verification. The mempool accepts it while the flag-gated path would reject it, breaking the intended pre-activation enforcement.

**Corrupted result:** The concrete corrupted output is the boolean result of `op_secp256k1_verify` / `op_secp256r1_verify` — either `nil` (success) or `EvalErr::Secp256Failed` — being produced when the flag-controlled path would have returned `nil` (unknown-op no-op) or `EvalErr::Unimplemented`. [6](#0-5) 

---

### Likelihood Explanation

The entry path is fully attacker-controlled: any caller that submits CLVM bytes to `run_program` can choose to encode the secp opcode as a 4-byte atom. No special privileges, compromised nodes, or social engineering are required. The 4-byte encoding is a valid CLVM atom and will be parsed and dispatched normally. The bypass is triggered by a single opcode substitution in the serialized program.

---

### Recommendation

Add the `ENABLE_SECP_OPS` flag check to the 4-byte dispatch path, mirroring the guard already present on the 1-byte path:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

Without the flag, the 4-byte opcodes should fall through to `unknown_operator`, which returns a no-op with cost in consensus mode and `Unimplemented` in mempool mode — consistent with the intended pre-activation behavior.

---

### Proof of Concept

Craft a CLVM program that invokes secp256k1 verification using the 4-byte opcode encoding, and run it with `ChiaDialect::new(ClvmFlags::empty())` (no `ENABLE_SECP_OPS`):

```
; 4-byte opcode atom: 0x13d61f00 = op_secp256k1_verify via cost-formula path
; args: (pubkey msg sig)
(0x13d61f00 pubkey msg sig)
```

With `ClvmFlags::empty()` (no `ENABLE_SECP_OPS`):
- **1-byte path** (opcode 64): falls to `unknown_operator` → no-op / `Unimplemented`
- **4-byte path** (opcode `0x13d61f00`): dispatches `op_secp256k1_verify` directly, executes real ECDSA verification

The same program with a valid signature returns `nil` (success) via the 4-byte path, while a node treating `0x13d61f00` as an unknown op also returns `nil` but for a completely different reason (no-op). With an invalid signature, the 4-byte path raises `EvalErr::Secp256Failed` while the unknown-op path succeeds — a direct consensus split on the same program bytes. [7](#0-6) [8](#0-7)

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

**File:** src/secp_ops.rs (L51-57)
```rust
    let result = verifier.verify_prehash(msg.as_ref(), &sig);

    if result.is_err() {
        Err(EvalErr::Secp256Failed(input))?
    } else {
        Ok(Reduction(cost, a.nil()))
    }
```

**File:** src/secp_ops.rs (L96-103)
```rust
    // verify signature
    let result = verifier.verify_prehash(msg.as_ref(), &sig);

    if result.is_err() {
        Err(EvalErr::Secp256Failed(input))?
    } else {
        Ok(Reduction(cost, a.nil()))
    }
```

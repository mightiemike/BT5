### Title
`ENABLE_SECP_OPS` Flag Check Missing on 4-Byte Secp Opcode Dispatch Path — (`File: src/chia_dialect.rs`)

### Summary

`ChiaDialect::op()` gates the secp operators behind `ENABLE_SECP_OPS` only for their 1-byte opcode aliases (64, 65). The same operators are also reachable via their 4-byte canonical opcodes (`0x13d61f00` for `secp256k1_verify`, `0x1c3a8f00` for `secp256r1_verify`), and that dispatch branch performs no flag check at all. Any attacker-controlled CLVM program can invoke full secp signature verification before the hard-fork flag activates, bypassing the intended activation gate.

### Finding Description

In `src/chia_dialect.rs`, `ChiaDialect::op()` has two separate dispatch paths for secp operators.

**1-byte opcode path (gated):**

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
``` [1](#0-0) 

**4-byte opcode path (ungated):**

```rust
if op_len == 4 {
    let b = allocator.atom(o);
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,
        0x1c3a8f00 => op_secp256r1_verify,
        _ => { return unknown_operator(...); }
    };
    return f(allocator, argument_list, max_cost, flags);
}
``` [2](#0-1) 

There is no `flags.contains(ClvmFlags::ENABLE_SECP_OPS)` guard anywhere in the 4-byte branch. The same `op_secp256k1_verify` / `op_secp256r1_verify` functions are called unconditionally. The `ENABLE_SECP_OPS` flag is defined as a hard-fork activation control:

```rust
/// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
const ENABLE_SECP_OPS = 0x0800;
``` [3](#0-2) 

The flag is exposed to Python callers and is the documented mechanism for activating secp operators: [4](#0-3) 

### Impact Explanation

**Impact: High**

An attacker-controlled CLVM program that encodes the 4-byte opcode `0x13d61f00` or `0x1c3a8f00` as its operator atom will invoke full secp signature verification regardless of whether `ENABLE_SECP_OPS` is set. Concretely:

- **Hard-fork gate bypassed**: The `ENABLE_SECP_OPS` flag is the intended activation mechanism for secp operators. Via 4-byte opcodes, secp operators execute on every node, pre- and post-hard-fork, with no flag required. The activation timeline is violated.
- **Mempool/consensus asymmetry**: Nodes running in mempool mode (`MEMPOOL_MODE` does not include `ENABLE_SECP_OPS`) will accept and execute secp verification via 4-byte opcodes, while rejecting the same logic expressed via 1-byte opcodes. This creates an inconsistent validation surface.
- **Operator wiring error**: The `ENABLE_SECP_OPS` flag provides a false sense of control. Callers (including `chia-blockchain`) that set flags to control secp availability cannot actually prevent secp execution via the 4-byte path.

### Likelihood Explanation

**Likelihood: High**

The 4-byte opcode values are derived from the documented cost formula and are visible in the source. Any attacker who reads `src/chia_dialect.rs` can construct a CLVM atom of length 4 with value `0x13d61f00` or `0x1c3a8f00` and submit it as an operator in a CLVM program. No privileged access, keys, or social engineering is required. The entry point is the standard `run_serialized_chia_program` Python API or any Rust caller of `run_program`.

### Recommendation

Add the `ENABLE_SECP_OPS` guard to the 4-byte opcode dispatch branch, mirroring the 1-byte path:

```diff
 let f = match opcode {
-    0x13d61f00 => op_secp256k1_verify,
-    0x1c3a8f00 => op_secp256r1_verify,
+    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
+    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
     _ => {
         return unknown_operator(allocator, o, argument_list, flags, max_cost);
     }
 };
```

This ensures that both opcode representations of secp operators are uniformly gated behind the hard-fork flag.

### Proof of Concept

Construct a CLVM program whose operator atom is the 4-byte big-endian encoding of `0x13d61f00`, with valid secp256k1 arguments. Run it via `run_serialized_chia_program` with `flags = 0` (no `ENABLE_SECP_OPS`):

```python
from clvm_rs import run_serialized_chia_program, ENABLE_SECP_OPS

# Serialize

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

**File:** wheel/src/api.rs (L321-321)
```rust
    m.add("ENABLE_SECP_OPS", ClvmFlags::ENABLE_SECP_OPS.bits())?;
```

### Title
Secp Operator Access Control Bypass via 4-Byte Opcode Path — (File: `src/chia_dialect.rs`)

### Summary

`ChiaDialect::op()` exposes two independent dispatch paths to `op_secp256k1_verify` and `op_secp256r1_verify`. The 1-byte opcode aliases (opcodes 64 and 65) are correctly gated behind `ClvmFlags::ENABLE_SECP_OPS`. The 4-byte opcode path (opcodes `0x13d61f00` and `0x1c3a8f00`) dispatches to the same functions with **no flag check at all**. Any attacker-controlled CLVM program can invoke secp signature verification unconditionally by using the 4-byte encoding, regardless of whether the caller set `ENABLE_SECP_OPS`.

---

### Finding Description

In `ChiaDialect::op()`, the 4-byte opcode branch is reached first (before the 1-byte branch) and dispatches directly to the secp functions: [1](#0-0) 

```rust
if op_len == 4 {
    let b = allocator.atom(o);
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,   // no flag check
        0x1c3a8f00 => op_secp256r1_verify,   // no flag check
        _ => {
            return unknown_operator(allocator, o, argument_list, flags, max_cost);
        }
    };
    return f(allocator, argument_list, max_cost, flags);
}
```

The 1-byte aliases, by contrast, are correctly gated: [2](#0-1) 

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

The flag is defined and documented as the hard-fork gate for secp operations: [3](#0-2) 

And it is exported to Python callers as a first-class API constant: [4](#0-3) 

The Python-facing `run_serialized_chia_program` accepts a raw `u32` flags field and constructs `ChiaDialect` from it: [5](#0-4) 

A caller who passes flags without `ENABLE_SECP_OPS` set — expecting secp operations to be unavailable — is silently wrong. The 4-byte opcode path is always reachable.

---

### Impact Explanation

- **Unauthorized secp signature verification**: A CLVM program submitted by an attacker can call `op_secp256k1_verify` or `op_secp256r1_verify` (cost 1,850,000 and 1,300,000 respectively) even when the executing node has not activated `ENABLE_SECP_OPS`. This undermines the hard-fork gating mechanism.
- **Consensus divergence**: If one node evaluates a program using the 4-byte opcode path (always succeeds) while another node running different logic rejects it (expecting secp ops to be gated), the two nodes reach different conclusions about the validity of the same spend — a direct consensus split.
- **False security boundary**: Python callers and downstream integrators who inspect the exported `ENABLE_SECP_OPS` constant and omit it from their flags believe they have disabled secp operations. They have not.

---

### Likelihood Explanation

Low to moderate. The attacker must be able to submit attacker-controlled CLVM bytes to a node running without `ENABLE_SECP_OPS`. This is realistic: any puzzle spend on the Chia network is attacker-controlled CLVM. The 4-byte opcode encoding is not obscure — it is the original secp encoding documented in the source comments. A motivated attacker who reads the source can construct the bypass trivially.

---

### Recommendation

Add an `ENABLE_SECP_OPS` flag check to the 4-byte opcode dispatch branch, mirroring the check on the 1-byte aliases:

```rust
if op_len == 4 {
    let b = allocator.atom(o);
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
        0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
        _ => {
            return unknown_operator(allocator, o, argument_list, flags, max_cost);
        }
    };
    return f(allocator, argument_list, max_cost, flags);
}
```

If backward compatibility with the 4-byte encoding must be preserved unconditionally (i.e., the 4-byte opcodes were always valid before `ENABLE_SECP_OPS` existed), introduce a separate flag (e.g., `ENABLE_SECP_4BYTE_OPS`) and document clearly that `ENABLE_SECP_OPS` does **not** gate the 4-byte path. Update the Python API exports and all downstream documentation accordingly.

---

### Proof of Concept

Attacker constructs a CLVM program using the 4-byte opcode `0x13d61f00`:

```
; CLVM serialized: opcode atom = bytes [0x13, 0xd6, 0x1f, 0x00]
; (0x13d61f00 pubkey message signature)
```

Caller invokes:

```python
from clvm_rs import run_serialized_chia_program, NO_UNKNOWN_OPS, LIMIT_HEAP, CANONICAL_INTS

# Deliberately omit ENABLE_SECP_OPS — caller believes secp ops are disabled
flags = NO_UNKNOWN_OPS | LIMIT_HEAP | CANONICAL_INTS

cost, result = run_serialized_chia_program(
    program_bytes,   # contains 4-byte opcode 0x13d61f00
    args_bytes,
    max_cost=10_000_000,
    flags=flags,
)
# op_secp256k1_verify executes successfully despite ENABLE_SECP_OPS not being set
```

The 4-byte opcode branch in `ChiaDialect::op()` is reached at: [6](#0-5) 

and dispatches to `op_secp256k1_verify` without consulting `flags` for `ENABLE_SECP_OPS`, while the 1-byte alias at: [2](#0-1) 

would have been correctly blocked. The broken invariant is: **`ENABLE_SECP_OPS` unset ⟹ secp operations unavailable** — this invariant is violated by the 4-byte opcode path.

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

**File:** wheel/src/api.rs (L40-62)
```rust
pub fn run_serialized_chia_program(
    py: Python,
    program: &[u8],
    args: &[u8],
    max_cost: Cost,
    flags: u32,
) -> PyResult<(u64, LazyNode)> {
    let flags = ClvmFlags::from_bits_truncate(flags);
    let mut allocator = if flags.contains(ClvmFlags::LIMIT_HEAP) {
        Allocator::new_limited(500000000)
    } else {
        Allocator::new()
    };

    let r: Response = (|| -> PyResult<Response> {
        let program = node_from_bytes(&mut allocator, program).map_err(eval_to_py)?;
        let args = node_from_bytes(&mut allocator, args).map_err(eval_to_py)?;
        let dialect = ChiaDialect::new(flags);

        Ok(py.detach(|| run_program(&mut allocator, &dialect, program, args, max_cost)))
    })()?;
    adapt_response(py, allocator, r)
}
```

**File:** wheel/src/api.rs (L321-321)
```rust
    m.add("ENABLE_SECP_OPS", ClvmFlags::ENABLE_SECP_OPS.bits())?;
```

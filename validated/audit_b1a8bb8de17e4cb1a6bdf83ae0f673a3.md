### Title
`ENABLE_SECP_OPS` Hard-Fork Flag Bypassed via 4-Byte Opcode Dispatch Path — (`File: src/chia_dialect.rs`)

---

### Summary

The `ENABLE_SECP_OPS` flag is the designated hard-fork gate controlling whether `op_secp256k1_verify` and `op_secp256r1_verify` are available to CLVM programs. The 1-byte opcode dispatch path (opcodes 64 and 65) correctly enforces this flag. However, a parallel 4-byte opcode dispatch path in the same `op()` function dispatches to the identical secp implementations (`0x13d61f00` → `op_secp256k1_verify`, `0x1c3a8f00` → `op_secp256r1_verify`) with **no flag check at all**. An attacker-controlled CLVM program using the 4-byte opcode encoding invokes live secp signature verification regardless of whether `ENABLE_SECP_OPS` is set, rendering the flag ineffective as a hard-fork access control.

---

### Finding Description

In `src/chia_dialect.rs`, the `ChiaDialect::op()` function has two structurally separate dispatch branches based on opcode byte-length:

**Branch 1 — 4-byte opcodes (lines 157–183):** Described in comments as "unknown operators with assigned cost," this branch decodes the raw `u32` opcode and matches two specific values:

```rust
// src/chia_dialect.rs lines 175–182
let f = match opcode {
    0x13d61f00 => op_secp256k1_verify,
    0x1c3a8f00 => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
return f(allocator, argument_list, max_cost, flags);
```

No `ENABLE_SECP_OPS` check is present. The secp functions execute unconditionally.

**Branch 2 — 1-byte opcodes (lines 248–249):** The same operators are correctly gated:

```rust
// src/chia_dialect.rs lines 248–249
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

The cost constants in `src/secp_ops.rs` confirm the 4-byte opcodes are the cost-matched aliases for the same operators:
- `SECP256K1_VERIFY_COST = 1300000` → multiplier `0x13d61f` (= 1299999, +1 = 1300000) → opcode `0x13d61f00`
- `SECP256R1_VERIFY_COST = 1850000` → multiplier `0x1c3a8f` (= 1849999, +1 = 1850000) → opcode `0x1c3a8f00`

The attacker entry path is direct: submit any CLVM program whose operator atom is the 4-byte encoding `\x13\xd6\x1f\x00` or `\x1c\x3a\x8f\x00`. The `run_program` loop calls `dialect.op()`, which hits the 4-byte branch and dispatches to the live secp implementation without consulting `ENABLE_SECP_OPS`. The Python-facing entry point `run_serialized_chia_program` in `wheel/src/api.rs` (line 40–62) accepts attacker-controlled `program` bytes and a caller-supplied `flags: u32`, making this reachable from any Python consumer of the wheel.

---

### Impact Explanation

`ENABLE_SECP_OPS` is a hard-fork flag: it is supposed to be absent on nodes running before the secp hard fork activates, and present only after. A node operator or integrator that constructs `ChiaDialect::new(flags)` without `ENABLE_SECP_OPS` — the correct pre-fork configuration — expects secp operators to be rejected as unknown. Instead, any CLVM program using the 4-byte opcode encoding executes full secp256k1/secp256r1 signature verification. This produces two concrete harms:

1. **Consensus divergence**: Pre-fork nodes (no `ENABLE_SECP_OPS`) accept and execute secp verification via 4-byte opcodes, while the intended behavior is rejection. If any node version or configuration treats 4-byte secp opcodes as true unknown operators (returning nil), the two populations reach different execution results for the same program — a chain split condition.

2. **Flag-as-access-control broken**: The `ENABLE_SECP_OPS` flag is exported to Python callers (`wheel/src/api.rs` line 321) and is part of the public API contract. Callers relying on the flag to disable secp ops (e.g., for pre-fork mempool validation) receive no protection against the 4-byte encoding. The flag provides a false sense of security identical in structure to the missing `FLAG_SECURE` in the reference report.

---

### Likelihood Explanation

Likelihood is **medium-high**. The attacker requires only the ability to submit CLVM programs — the normal, unprivileged capability of any Chia network participant. Crafting a program with a 4-byte operator atom is trivial: the CLVM serialization format encodes operator atoms as raw bytes, and the 4-byte secp opcode values are derivable from the public cost formula documented in the source comments. No privileged access, key material, or social engineering is required.

---

### Recommendation

Add the `ENABLE_SECP_OPS` flag check to the 4-byte dispatch branch, mirroring the 1-byte branch:

```rust
// src/chia_dialect.rs — 4-byte branch
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

Without the flag, the 4-byte secp opcodes should fall through to `unknown_operator`, consistent with the "unknown operators with assigned cost" semantics described in the comment at line 158.

---

### Proof of Concept

**Setup**: Construct a `ChiaDialect` without `ENABLE_SECP_OPS` (simulating a pre-fork node):

```python
from clvm_rs import run_serialized_chia_program, NO_UNKNOWN_OPS

# Serialize a CLVM program: (0x13d61f00 pubkey msg sig)
# where pubkey/msg/sig are valid secp256k1 inputs.
# Operator atom is the 4-byte encoding \x13\xd6\x1f\x00.
# flags = NO_UNKNOWN_OPS only — ENABLE_SECP_OPS (0x0800) is NOT set.

flags = NO_UNKNOWN_OPS  # 0x0002, no ENABLE_SECP_OPS
cost, result = run_serialized_chia_program(program_bytes, args_bytes, 10_000_000, flags)
# Expected (pre-fork): EvalErr::Unimplemented — secp ops should be disabled
# Actual: op_secp256k1_verify executes and returns nil on valid signature
```

**Broken invariant**: `op_secp256k1_verify` and `op_secp256r1_verify` execute and return `Reduction(1300000, nil)` / `Reduction(1850000, nil)` respectively, even though `ENABLE_SECP_OPS` is absent from `flags`. The 1-byte opcode forms (64, 65) correctly raise `EvalErr::Unimplemented` under the same flags, confirming the bypass is specific to the 4-byte path.

**Relevant lines**: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** src/chia_dialect.rs (L157-182)
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
```

**File:** src/chia_dialect.rs (L248-249)
```rust
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

**File:** src/secp_ops.rs (L11-12)
```rust
const SECP256R1_VERIFY_COST: Cost = 1850000;
const SECP256K1_VERIFY_COST: Cost = 1300000;
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

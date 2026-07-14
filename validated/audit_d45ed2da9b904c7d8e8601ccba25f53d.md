### Title
`ENABLE_SECP_OPS` Flag Bypassed via 4-Byte Opcode Form, Allowing Unauthorized Secp Verification — (`File: src/chia_dialect.rs`)

---

### Summary

`ChiaDialect::op()` dispatches secp256k1 and secp256r1 verification through two separate code paths: a 1-byte opcode path (opcodes 64/65) that correctly checks `ClvmFlags::ENABLE_SECP_OPS`, and a 4-byte opcode path (`0x13d61f00` / `0x1c3a8f00`) that dispatches directly to the same secp functions **without any flag check**. An attacker can craft a CLVM program using the 4-byte opcode encoding to invoke secp signature verification regardless of whether `ENABLE_SECP_OPS` is set, bypassing the soft-fork activation gate entirely.

---

### Finding Description

In `src/chia_dialect.rs`, the `op()` method of `ChiaDialect` handles 4-byte opcodes first, before the 1-byte opcode table:

```rust
// lines 157–182
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

The 1-byte opcode path, by contrast, correctly gates the same functions:

```rust
// lines 248–249
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

The `ENABLE_SECP_OPS` flag is documented as the activation gate for secp operations. The 4-byte form is the "unknown operator with assigned cost" encoding of the same operations — it encodes the cost multiplier directly in the opcode bytes — but it calls the identical `op_secp256k1_verify` / `op_secp256r1_verify` functions. Because the 4-byte dispatch branch never consults `ENABLE_SECP_OPS`, the flag provides no actual protection: any CLVM program that uses the 4-byte opcode encoding executes secp verification unconditionally.

Additionally, the 4-byte branch does not fall through to `unknown_operator()` for the secp opcodes, so the `NO_UNKNOWN_OPS` restriction present in `MEMPOOL_MODE` also does not apply. A program using the 4-byte secp opcode form will execute in mempool mode even when `ENABLE_SECP_OPS` is absent from the flags.

`MEMPOOL_MODE` is defined as:

```rust
// lines 72–76
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

`ENABLE_SECP_OPS` is not included in `MEMPOOL_MODE`, so the mempool is supposed to reject secp ops when the soft-fork has not activated. The 4-byte bypass defeats this.

---

### Impact Explanation

**Consensus divergence / soft-fork gate bypass.** The `ENABLE_SECP_OPS` flag is the mechanism by which the Chia node controls whether secp operations are valid before the corresponding soft-fork activates. If a node passes flags without `ENABLE_SECP_OPS` (pre-activation), it expects `clvm_rs` to reject any secp operation. A CLVM program using the 4-byte opcode encoding (`0x13d61f00` or `0x1c3a8f00`) will instead succeed, causing the node to accept a spend that should be invalid. This can produce a consensus split between nodes that enforce the flag correctly at a higher layer and nodes that rely on `clvm_rs` to enforce it. It also means the mempool accepts secp-bearing transactions before the soft-fork activates, violating the mempool's stricter validation contract.

---

### Likelihood Explanation

The 4-byte opcode values are derivable from the cost formula documented in the code comments. Any attacker who reads `chia_dialect.rs` can compute `0x13d61f00` and `0x1c3a8f00` and craft a serialized CLVM program that uses them. The entry point is `run_serialized_chia_program` in the Python API (`wheel/src/api.rs` line 40), which accepts arbitrary attacker-controlled bytes and a caller-supplied `flags` integer. No privileged access is required; the attacker only needs to submit a crafted transaction to the mempool.

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

This ensures that both opcode encodings of the secp operators are subject to the same activation gate, and that `NO_UNKNOWN_OPS` / mempool mode correctly rejects them when the flag is absent.

---

### Proof of Concept

1. Serialize a valid CLVM program that invokes opcode `0x13d61f00` (secp256k1_verify in 4-byte form) with a valid pubkey, message digest, and signature.
2. Call `run_serialized_chia_program(program, args, max_cost, flags=0)` — i.e., with **no** `ENABLE_SECP_OPS` bit set.
3. Observe that the call succeeds and returns cost `1300061`, identical to the result when `ENABLE_SECP_OPS` is explicitly set.
4. Repeat with `flags = MEMPOOL_MODE` (which also lacks `ENABLE_SECP_OPS`) and observe the same bypass — the program is accepted by the mempool validator despite secp not being activated.

Expected (correct) behavior: both calls should return an "unimplemented operator" error, matching the behavior of the 1-byte opcode 64 without `ENABLE_SECP_OPS`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

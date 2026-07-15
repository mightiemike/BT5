### Title
Missing `ENABLE_SECP_OPS` Flag Check in 4-Byte Opcode Dispatch Bypasses Soft-Fork Gate for Secp Operators — (`File: src/chia_dialect.rs`)

---

### Summary

The `ChiaDialect::op()` function in `src/chia_dialect.rs` dispatches the secp256k1 and secp256r1 verification operators via their 4-byte opcode encodings (`0x13d61f00` and `0x1c3a8f00`) **without** checking the `ClvmFlags::ENABLE_SECP_OPS` flag. The 1-byte opcode path (opcodes 64 and 65) correctly gates these operators behind `ENABLE_SECP_OPS`, but the 4-byte path does not. This allows attacker-controlled CLVM bytes to invoke live secp signature verification before the soft-fork has activated, producing a consensus divergence: a program that should be a no-op (unknown operator returning nil) instead executes real cryptographic verification and may raise `Secp256Failed`.

---

### Finding Description

`ClvmFlags::ENABLE_SECP_OPS` is the soft-fork activation flag for the secp256k1 and secp256r1 verification operators. The design intent is that before the flag is set, any invocation of these operators must be treated as an unknown operator — a no-op returning nil with a deterministic cost — so that pre-fork nodes and post-fork nodes agree on program outcomes.

The `ChiaDialect::op()` function handles two opcode encodings for the secp operators:

**1-byte path (correctly gated):**
```rust
// src/chia_dialect.rs lines 248-249
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```
When `ENABLE_SECP_OPS` is absent, opcodes 64 and 65 fall through to `unknown_operator`, returning nil with cost. This is correct.

**4-byte path (missing gate):**
```rust
// src/chia_dialect.rs lines 175-181
let f = match opcode {
    0x13d61f00 => op_secp256k1_verify,
    0x1c3a8f00 => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
return f(allocator, argument_list, max_cost, flags);
```
There is **no** `flags.contains(ClvmFlags::ENABLE_SECP_OPS)` guard here. The 4-byte opcodes `0x13d61f00` and `0x1c3a8f00` are the "unknown operator with assigned cost" encodings of the secp operators — their cost formula yields exactly 1,300,000 and 1,850,000 respectively, matching the operators' actual costs. Before the soft-fork, these bytes should be treated as unknown operators (no-ops). Instead, they unconditionally dispatch to `op_secp256k1_verify` and `op_secp256r1_verify`.

Neither `op_secp256k1_verify` nor `op_secp256r1_verify` checks `ENABLE_SECP_OPS` internally — both accept `_flags: ClvmFlags` and ignore it entirely:
```rust
// src/secp_ops.rs lines 15-19, 61-65
pub fn op_secp256r1_verify(a: &mut Allocator, input: NodePtr, max_cost: Cost, _flags: ClvmFlags) -> Response { ... }
pub fn op_secp256k1_verify(a: &mut Allocator, input: NodePtr, max_cost: Cost, _flags: ClvmFlags) -> Response { ... }
```

The `flags` variable passed into the 4-byte dispatch block is computed at lines 144–154 and includes the `extension` (softfork guard) flags, but `ENABLE_SECP_OPS` is never injected by any `OperatorSet` variant, so it is absent whenever the caller did not set it.

---

### Impact Explanation

**High.** This is a consensus divergence. Before the secp soft-fork activates (i.e., when `ENABLE_SECP_OPS` is not set by the full node), a CLVM program using the 4-byte secp opcode form with an invalid or malformed signature will raise `EvalErr::Secp256Failed` or `EvalErr::InvalidOpArg` — causing the spend to be rejected — when the correct pre-fork behavior is to return nil with cost (no-op). A program that should be valid on all pre-fork nodes is instead rejected, breaking consensus between nodes that have and have not applied the soft-fork flag. Conversely, a program with a valid signature silently executes real cryptographic verification pre-fork, which is also incorrect behavior (the operator should not exist yet). Any coin puzzle that embeds the 4-byte secp opcode form is affected.

---

### Likelihood Explanation

**Low.** The soft-fork must not yet have activated (i.e., `ENABLE_SECP_OPS` must be absent from the dialect flags passed by the full node). In practice, once the fork activates, the behavior of both paths converges. The window of exposure is the period between when the code is deployed and when the fork activates on-chain. A puzzle author who uses the 4-byte opcode form (rather than the 1-byte form) during this window, or an attacker who crafts such bytes, can trigger the divergence.

---

### Recommendation

Add the `ENABLE_SECP_OPS` guard to the 4-byte opcode dispatch in `src/chia_dialect.rs`, mirroring the 1-byte path:

```rust
// src/chia_dialect.rs, inside the op_len == 4 block
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

This ensures that before the soft-fork activates, the 4-byte secp opcodes are treated as unknown operators (no-ops returning nil with cost), consistent with the 1-byte opcode path and the design intent documented in `docs/new-operator-checklist.md`.

---

### Proof of Concept

**Entry path:** Caller submits CLVM bytes encoding a program that invokes opcode `0x13d61f00` (4-byte secp256k1 opcode) with an invalid signature, using a `ChiaDialect` constructed **without** `ENABLE_SECP_OPS` (pre-fork node configuration).

**Execution trace:**

1. `run_program` evaluates the program and reaches `apply_op`.
2. `apply_op` calls `self.dialect.op(allocator, operator, operand_list, max_cost, current_extensions)`.
3. `ChiaDialect::op` computes `flags = self.flags | ...` — `ENABLE_SECP_OPS` is absent.
4. `allocator.atom_len(o) == 4`, so the 4-byte branch is taken.
5. `opcode == 0x13d61f00` matches `op_secp256k1_verify` unconditionally — **no flag check**.
6. `op_secp256k1_verify` runs real ECDSA verification; the invalid signature causes `EvalErr::Secp256Failed`.
7. The spend is rejected.

**Expected behavior (pre-fork):** `0x13d61f00` falls through to `unknown_operator`, which returns `Ok(Reduction(1300000, nil))` — the spend succeeds.

**Corrupted result:** `EvalErr::Secp256Failed` instead of `Ok(Reduction(1300000, nil))`.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** src/chia_dialect.rs (L144-154)
```rust
        let flags = self.flags
            | match extension {
                // This is the default set of operators, so no special flags need to be added.
                OperatorSet::Default => ClvmFlags::empty(),

                // Since BLS has been hardforked in universally, this has no effect.
                OperatorSet::Bls => ClvmFlags::empty(),

                // Keccak is allowed as if it were a default operator, inside of the softfork guard.
                OperatorSet::Keccak => ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD,
            };
```

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

**File:** src/chia_dialect.rs (L246-249)
```rust
            62 if flags.contains(ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD) => op_keccak256,
            63 if flags.contains(ClvmFlags::ENABLE_SHA256_TREE) => op_sha256_tree,
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

**File:** src/secp_ops.rs (L15-19)
```rust
pub fn op_secp256r1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
```

**File:** src/secp_ops.rs (L61-65)
```rust
pub fn op_secp256k1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
```

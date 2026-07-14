### Title
Missing `ENABLE_SECP_OPS` Flag Check on 4-Byte Secp Opcode Dispatch Path — (`File: src/chia_dialect.rs`)

---

### Summary

The `ChiaDialect::op()` function in `src/chia_dialect.rs` dispatches secp signature-verification operators via two distinct encoding paths. The 1-byte opcode path (opcodes 64 and 65) correctly gates execution behind `ClvmFlags::ENABLE_SECP_OPS`. The 4-byte opcode path (`0x13d61f00` for `secp256k1_verify`, `0x1c3a8f00` for `secp256r1_verify`) dispatches to the same underlying functions with **no flag check at all**. Any caller that constructs a `ChiaDialect` without `ENABLE_SECP_OPS` — including the standard `MEMPOOL_MODE` — still executes live secp verification when attacker-controlled CLVM bytes use the 4-byte encoding.

---

### Finding Description

`ChiaDialect::op()` has two separate branches for secp operators.

**Branch A — 1-byte opcodes (flag-gated, correct):** [1](#0-0) 

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

Without the flag these arms are skipped; the operator falls through to `unknown_operator`.

**Branch B — 4-byte opcodes (no flag check, broken):** [2](#0-1) 

```rust
if op_len == 4 {
    ...
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,
        0x1c3a8f00 => op_secp256r1_verify,
        _ => { return unknown_operator(...); }
    };
    return f(allocator, argument_list, max_cost, flags); // no ENABLE_SECP_OPS check
}
```

The prerequisite flag check that guards Branch A is entirely absent here. Both branches call the same `op_secp256k1_verify` / `op_secp256r1_verify` implementations in `src/secp_ops.rs`. [3](#0-2) 

The `ENABLE_SECP_OPS` flag is defined as a hard-fork guard: [4](#0-3) 

The standard `MEMPOOL_MODE` constant does **not** include `ENABLE_SECP_OPS`: [5](#0-4) 

So in mempool mode, 1-byte secp opcodes are blocked (they fall to `unknown_operator`, which with `NO_UNKNOWN_OPS` returns `EvalErr::Unimplemented`), but 4-byte secp opcodes execute real cryptographic verification unconditionally.

---

### Impact Explanation

**Flag/operator wiring error producing consensus and mempool divergence.**

- A CLVM program using 1-byte opcode 64 without `ENABLE_SECP_OPS` in consensus mode is treated as an unknown no-op (returns nil, charges unknown-op cost, does not verify). The same program rewritten to use 4-byte opcode `0x13d61f00` performs actual secp256k1 signature verification and returns an error if the signature is invalid. The two encodings of the same logical operation produce different results under the same dialect configuration — a direct consensus split.
- In mempool mode, 1-byte secp opcodes are hard-rejected (`Unimplemented`), while 4-byte secp opcodes succeed or fail based on the actual signature. An attacker can craft a coin puzzle that passes mempool validation via the 4-byte path but whose semantic meaning differs from what the mempool intended to evaluate.
- The corrupted result is concrete: the `Response` returned by `op_secp256k1_verify` / `op_secp256r1_verify` (either `Ok(Reduction(cost, nil))` or `Err(Secp256Failed)`) is produced when the flag-gated contract says it should not be.

---

### Likelihood Explanation

The entry path is fully attacker-controlled: any CLVM program submitted to a node or mempool can embed the 4-byte byte sequences `\x13\xd6\x1f\x00` or `\x1c\x3a\x8f\x00` as operator atoms. No special privilege, configuration change, or social engineering is required. The 4-byte encoding is a valid, parseable CLVM atom. The divergence is triggered on every execution of such a program on a node running without `ENABLE_SECP_OPS`.

---

### Recommendation

Add the same `ENABLE_SECP_OPS` guard to the 4-byte dispatch branch:

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

This makes both encoding paths honour the same prerequisite flag, eliminating the divergence.

---

### Proof of Concept

1. Construct a `ChiaDialect` **without** `ENABLE_SECP_OPS` (e.g., `MEMPOOL_MODE`).
2. Build a CLVM program whose operator atom is the 4-byte sequence `\x13\xd6\x1f\x00` with a valid secp256k1 pubkey, message digest, and signature as arguments.
3. Call `run_program`. Observe that `op_secp256k1_verify` executes and returns `Ok(Reduction(1300000, nil))`.
4. Repeat with 1-byte opcode `\x40` (64) under the same dialect. In mempool mode, observe `EvalErr::Unimplemented`; in consensus mode, observe the unknown-op no-op path — neither performs actual verification.

The two encodings of the same operator produce different `Response` values under identical dialect flags, confirming the missing prerequisite check. [6](#0-5) [1](#0-0)

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

**File:** src/secp_ops.rs (L61-103)
```rust
pub fn op_secp256k1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let cost = SECP256K1_VERIFY_COST;
    check_cost(cost, max_cost)?;

    let [pubkey, msg, sig] = get_args::<3>(a, input, "secp256k1_verify")?;

    // first argument is sec1 encoded pubkey
    let pubkey = atom(a, pubkey, "secp256k1_verify pubkey")?;
    let verifier = K1VerifyingKey::from_sec1_bytes(pubkey.as_ref()).map_err(|_| {
        EvalErr::InvalidOpArg(input, "secp256k1_verify: pubkey is not valid".to_string())
    })?;

    // second arg is message
    let msg = atom(a, msg, "secp256k1_verify msg")?;
    if msg.as_ref().len() != 32 {
        Err(EvalErr::InvalidOpArg(
            input,
            "secp256k1_verify: message digest is not 32 bytes".to_string(),
        ))?;
    }

    // third arg is a fixed-size signature
    let sig = atom(a, sig, "secp256k1_verify sig")?;
    let sig = K1Signature::from_slice(sig.as_ref()).map_err(|_| {
        EvalErr::InvalidOpArg(
            input,
            "secp256k1_verify: signature is not valid".to_string(),
        )
    })?;

    // verify signature
    let result = verifier.verify_prehash(msg.as_ref(), &sig);

    if result.is_err() {
        Err(EvalErr::Secp256Failed(input))?
    } else {
        Ok(Reduction(cost, a.nil()))
    }
```

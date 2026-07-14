### Title
`ENABLE_SECP_OPS` Flag Gates 1-Byte Secp Opcodes But Not Functionally Identical 4-Byte Encodings, Enabling Bypass - (File: `src/chia_dialect.rs`)

### Summary

The `ENABLE_SECP_OPS` flag in `ChiaDialect` is supposed to control availability of secp256k1 and secp256r1 verification operators. However, the flag check is only applied to the 1-byte opcode path (opcodes 64 and 65). The functionally identical 4-byte opcode encodings (`0x13d61f00` → `op_secp256k1_verify`, `0x1c3a8f00` → `op_secp256r1_verify`) are dispatched unconditionally, bypassing the flag entirely. This is a direct analog to the external report's incomplete authorization check: one access path is gated, the other is not.

---

### Finding Description

In `src/chia_dialect.rs`, the `ChiaDialect::op()` method dispatches operators through two structurally separate branches:

**Branch 1 — 4-byte opcodes (lines 157–183):** The code explicitly matches `0x13d61f00` → `op_secp256k1_verify` and `0x1c3a8f00` → `op_secp256r1_verify` and calls them directly. No `ENABLE_SECP_OPS` check is performed. [1](#0-0) 

**Branch 2 — 1-byte opcodes (lines 248–249):** Opcodes 64 and 65 are gated behind `flags.contains(ClvmFlags::ENABLE_SECP_OPS)`. Without the flag, they fall through to `unknown_operator()`. [2](#0-1) 

The `ENABLE_SECP_OPS` flag definition explicitly names only opcodes 64 and 65: [3](#0-2) 

The 4-byte opcodes call the exact same underlying functions as the 1-byte opcodes: [4](#0-3) [5](#0-4) 

The result is that the `ENABLE_SECP_OPS` gate is incomplete: it checks one opcode encoding path but not the other, for the same underlying operation.

---

### Impact Explanation

The broken invariant is: *"secp verification is only reachable when `ENABLE_SECP_OPS` is set."* The 4-byte encoding violates this invariant.

**Consensus divergence:** In consensus mode (lenient, `allow_unknown_ops = true`), a program using 1-byte opcode 64 with an invalid signature is treated as a no-op (unknown op with cost). The same program using 4-byte opcode `0x13d61f00` with an invalid signature fails with `EvalErr::Secp256Failed`. Two nodes evaluating the same logical intent but different byte encodings reach different outcomes.

**Mempool mode divergence:** In `MEMPOOL_MODE` (which sets `NO_UNKNOWN_OPS` but not `ENABLE_SECP_OPS`), 1-byte opcode 64/65 returns `EvalErr::Unimplemented`. The 4-byte form still executes the real secp verification and can return `EvalErr::Secp256Failed` or succeed — a different error class and a different execution path. [6](#0-5) 

The corrupted result is the `Response` value returned by `ChiaDialect::op()`: for the same secp operation, the response type (no-op cost, `Unimplemented`, or `Secp256Failed`) diverges based solely on which byte encoding the attacker chooses.

---

### Likelihood Explanation

The 4-byte opcode values are documented in the source code comments and are trivially constructable from attacker-controlled CLVM bytes. Any party submitting a CLVM program (e.g., a coin puzzle solution) can choose the 4-byte encoding. The entry path is direct: attacker-controlled bytes → `ChiaDialect::op()` → 4-byte branch → secp verification without flag check. [7](#0-6) 

---

### Recommendation

Apply the `ENABLE_SECP_OPS` flag check to the 4-byte opcode branch as well:

```rust
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
```

Alternatively, if the 4-byte forms are intentionally always available (pre-fork encoding), this asymmetry must be explicitly documented and the flag description updated to clarify it does not gate the 4-byte forms.

---

### Proof of Concept

```
# Without ENABLE_SECP_OPS set:

# 1-byte opcode 64 (secp256k1_verify) — falls through to unknown_operator
# In lenient mode: no-op with cost. In strict mode: Unimplemented error.
Program bytes: (64 pubkey msg sig)

# 4-byte opcode 0x13d61f00 (secp256k1_verify) — calls op_secp256k1_verify directly
# In both modes: actual signature verification; fails with Secp256Failed on bad sig.
Program bytes: (0x13d61f00 pubkey msg sig)
```

The attacker submits the 4-byte form to invoke real secp verification regardless of whether `ENABLE_SECP_OPS` is set, while the 1-byte form is blocked. The `ENABLE_SECP_OPS` gate is bypassed by choosing the alternate opcode encoding — a direct structural analog to the external report's `from`-address bypass via `transferFrom()`. [8](#0-7)

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

**File:** src/chia_dialect.rs (L156-183)
```rust
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
```

**File:** src/chia_dialect.rs (L248-249)
```rust
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

**File:** src/secp_ops.rs (L15-57)
```rust
pub fn op_secp256r1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let cost = SECP256R1_VERIFY_COST;
    check_cost(cost, max_cost)?;

    let [pubkey, msg, sig] = get_args::<3>(a, input, "secp256r1_verify")?;

    // first argument is sec1 encoded pubkey
    let pubkey = atom(a, pubkey, "secp256r1_verify pubkey")?;
    let verifier = P1VerifyingKey::from_sec1_bytes(pubkey.as_ref()).map_err(|_| {
        EvalErr::InvalidOpArg(input, "secp256r1_verify: pubkey is not valid".to_string())
    })?;

    // second arg is sha256 hash of message
    let msg = atom(a, msg, "secp256r1_verify msg")?;
    if msg.as_ref().len() != 32 {
        Err(EvalErr::InvalidOpArg(
            input,
            "secp256r1_verify: message digest is not 32 bytes".to_string(),
        ))?;
    }

    // third arg is a fixed-size signature
    let sig = atom(a, sig, "secp256r1_verify sig")?;
    let sig = P1Signature::from_slice(sig.as_ref()).map_err(|_| {
        EvalErr::InvalidOpArg(
            input,
            "secp256r1_verify: signature is not valid".to_string(),
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

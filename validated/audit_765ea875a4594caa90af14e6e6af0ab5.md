### Title
`ENABLE_SECP_OPS` Flag Bypass via 4-Byte Opcode Forms Allows Ungated Secp Signature Verification - (File: `src/chia_dialect.rs`)

### Summary
The `ENABLE_SECP_OPS` flag is intended to gate access to `secp256k1_verify` and `secp256r1_verify`. However, the 4-byte opcode dispatch path in `ChiaDialect::op()` invokes these same operators unconditionally — without checking `ENABLE_SECP_OPS` — when the attacker-controlled opcode bytes are `0x13d61f00` or `0x1c3a8f00`. Any caller that relies on the absence of `ENABLE_SECP_OPS` to prevent secp execution is silently bypassed.

### Finding Description

`ChiaDialect::op()` in `src/chia_dialect.rs` contains two separate dispatch branches for secp operators.

**Branch 1 — 1-byte opcodes (flag-gated, lines 248–249):**
```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
``` [1](#0-0) 

**Branch 2 — 4-byte opcodes (no flag check, lines 175–182):**
```rust
let f = match opcode {
    0x13d61f00 => op_secp256k1_verify,
    0x1c3a8f00 => op_secp256r1_verify,
    _ => { return unknown_operator(...); }
};
return f(allocator, argument_list, max_cost, flags);
``` [2](#0-1) 

Both branches call the identical underlying functions `op_secp256k1_verify` / `op_secp256r1_verify` in `src/secp_ops.rs`, which perform real ECDSA signature verification. [3](#0-2) 

The flag is documented as:
```rust
/// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
const ENABLE_SECP_OPS = 0x0800;
``` [4](#0-3) 

The flag only guards the 1-byte forms. The 4-byte forms `0x13d61f00` and `0x1c3a8f00` are dispatched unconditionally, making `ENABLE_SECP_OPS` an incomplete access control gate. Notably, `MEMPOOL_MODE` does not include `ENABLE_SECP_OPS`, so even the stricter mempool path does not block the 4-byte secp opcodes. [5](#0-4) 

### Impact Explanation

Any CLVM program submitted with attacker-controlled bytes encoding opcode `0x13d61f00` or `0x1c3a8f00` will execute full secp ECDSA verification regardless of whether the caller set `ENABLE_SECP_OPS`. This has two concrete consequences:

1. **Access control bypass**: Callers (e.g., pre-fork consensus nodes, mempool validators) that deliberately omit `ENABLE_SECP_OPS` to prevent secp execution will silently have it executed anyway via the 4-byte opcode path.

2. **Consensus divergence**: A node that treats `ENABLE_SECP_OPS` as the authoritative gate for secp availability will compute a different program result (or accept/reject a spend) compared to a node that knows about the 4-byte bypass. This is the direct analog to "anyone can mint" — the privileged operation (secp verification) is reachable without the required permission token (`ENABLE_SECP_OPS`).

### Likelihood Explanation

The trigger is straightforward: craft a CLVM program whose operator atom is the 4-byte sequence `\x13\xd6\x1f\x00` (secp256k1) or `\x1c\x3a\x8f\x00` (secp256r1) with valid pubkey/msg/sig arguments. This requires only knowledge of the opcode encoding, which is documented in the codebase itself. No privileged access, social engineering, or dependency compromise is needed. The entry point is the standard `run_program` API, reachable from attacker-controlled CLVM bytes. [6](#0-5) 

### Recommendation

Add the `ENABLE_SECP_OPS` flag check to the 4-byte opcode dispatch branch, mirroring the guard already present for the 1-byte forms:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

If the 4-byte forms are intentionally always-on for backward compatibility, this must be explicitly documented and the `ENABLE_SECP_OPS` flag comment must be corrected to reflect that it only gates the 1-byte aliases, not secp execution in general.

### Proof of Concept

Construct a CLVM program where the operator is the 4-byte atom `0x13d61f00` with a valid secp256k1 pubkey, 32-byte message digest, and signature as arguments. Run it via `run_program` with a `ChiaDialect` constructed with `ClvmFlags::empty()` (no `ENABLE_SECP_OPS`). The program will execute `op_secp256k1_verify` and return `nil` on success — identical to running opcode 64 with `ENABLE_SECP_OPS` set — demonstrating that the flag provides no actual barrier when the 4-byte opcode form is used. [7](#0-6) [8](#0-7)

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

**File:** src/run_program.rs (L564-573)
```rust
pub fn run_program<'a, D: Dialect>(
    allocator: &'a mut Allocator,
    dialect: &'a D,
    program: NodePtr,
    env: NodePtr,
    max_cost: Cost,
) -> Response {
    let mut rpc = RunProgramContext::new(allocator, dialect);
    rpc.run_program(program, env, max_cost)
}
```

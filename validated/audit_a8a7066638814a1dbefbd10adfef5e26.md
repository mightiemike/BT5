### Title
`ENABLE_SECP_OPS` Flag Check Applied to 1-Byte Opcode Path but Bypassed via 4-Byte Opcode Path for the Same Operations - (`File: src/chia_dialect.rs`)

### Summary

`ChiaDialect::op()` gates `op_secp256k1_verify` and `op_secp256r1_verify` behind the `ENABLE_SECP_OPS` flag when dispatched via their 1-byte opcodes (64 and 65), but dispatches the identical functions unconditionally when invoked via their 4-byte opcode forms (`0x13d61f00` and `0x1c3a8f00`). Any attacker-controlled CLVM program can bypass the flag entirely by using the 4-byte opcode encoding.

### Finding Description

`ChiaDialect::op()` in `src/chia_dialect.rs` contains two separate dispatch branches for secp operations.

The 4-byte opcode branch (lines 157–182) matches `0x13d61f00` and `0x1c3a8f00` and calls `op_secp256k1_verify` / `op_secp256r1_verify` with no flag check: [1](#0-0) 

The 1-byte opcode branch (lines 248–249) gates the same functions behind `ENABLE_SECP_OPS`: [2](#0-1) 

The flag definition explicitly names only opcodes 64 and 65: [3](#0-2) 

`MEMPOOL_MODE` does not include `ENABLE_SECP_OPS`: [4](#0-3) 

Consequently, in mempool mode a program using 1-byte opcode 64 falls through to `unknown_operator` and is rejected by `NO_UNKNOWN_OPS`, while the identical secp verification invoked via 4-byte opcode `0x13d61f00` is dispatched and executed without any flag check. The two encodings of the same operation produce different outcomes under the same flag set.

### Impact Explanation

The concrete corrupted result is the return value of `op_secp256k1_verify` / `op_secp256r1_verify`: the function executes and returns `nil` (verification success) or an `EvalErr::Secp256Failed` error when called via the 4-byte path, whereas the 1-byte path returns an `EvalErr::Unimplemented` (unknown-op rejection) under the same flags. This is a flag/operator wiring error producing consensus divergence: two programs that are semantically identical differ in their accepted/rejected status depending solely on which byte-length encoding of the opcode they use. A mempool node running with `MEMPOOL_MODE` (which includes `NO_UNKNOWN_OPS` but not `ENABLE_SECP_OPS`) will reject the 1-byte form but accept and execute the 4-byte form, breaking the invariant that the flag uniformly controls access to these operations. [5](#0-4) 

### Likelihood Explanation

The entry path is fully attacker-controlled: any CLVM byte sequence submitted to `run_program` can encode the 4-byte opcode `0x13d61f00` or `0x1c3a8f00`. No special privilege, configuration, or social engineering is required. The 4-byte opcode encoding is valid CLVM and is parsed by the standard deserializer. The vulnerable dispatch is a necessary step in `ChiaDialect::op()` for every operator call. [6](#0-5) 

### Recommendation

Add the same `ENABLE_SECP_OPS` guard to the 4-byte opcode dispatch branch before calling `op_secp256k1_verify` or `op_secp256r1_verify`:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

This ensures both opcode encodings of the same operation are subject to the same flag-based access control, eliminating the divergence. [1](#0-0) 

### Proof of Concept

Construct a CLVM program that invokes secp256k1 verification via the 4-byte opcode `0x13d61f00` with a valid pubkey, message digest, and signature. Run it under `ChiaDialect::new(MEMPOOL_MODE)` (which does not include `ENABLE_SECP_OPS`). The program executes `op_secp256k1_verify` and returns `nil` on success. Run the identical program re-encoded with 1-byte opcode `64` under the same flags: `run_program` returns `EvalErr::Unimplemented` because `NO_UNKNOWN_OPS` is set and opcode 64 falls through to `unknown_operator`. The two programs produce different results under identical flags, confirming the bypass. [7](#0-6)

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

**File:** src/chia_dialect.rs (L239-252)
```rust
            60 => {
                if flags.contains(ClvmFlags::DISABLE_OP) {
                    return Err(EvalErr::Unimplemented(o))?;
                }
                op_modpow
            }
            61 => op_mod,
            62 if flags.contains(ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD) => op_keccak256,
            63 if flags.contains(ClvmFlags::ENABLE_SHA256_TREE) => op_sha256_tree,
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
            _ => {
                return unknown_operator(allocator, o, argument_list, flags, max_cost);
            }
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

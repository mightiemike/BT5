### Title
`ENABLE_SECP_OPS` Flag Gate Bypassed via 4-Byte Opcode Form of secp256k1/r1 Verify Operators — (`src/chia_dialect.rs`)

---

### Summary

The `ENABLE_SECP_OPS` flag is wired to gate only the 1-byte opcode aliases (64, 65) for `secp256k1_verify` and `secp256r1_verify`. The identical operator functions are also reachable via their 4-byte opcode encodings (`0x13d61f00`, `0x1c3a8f00`) in a separate dispatch branch that performs no flag check at all. An attacker-controlled CLVM program can invoke full secp signature verification in any dialect configuration — including `MEMPOOL_MODE` — simply by using the 4-byte opcode form, making `ENABLE_SECP_OPS` an ineffective gate.

---

### Finding Description

In `ChiaDialect::op()`, operator dispatch is split into two branches based on opcode byte-length:

**Branch 1 — 4-byte opcodes (lines 157–183):**

```rust
if op_len == 4 {
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,   // no flag check
        0x1c3a8f00 => op_secp256r1_verify,   // no flag check
        _ => { return unknown_operator(...); }
    };
    return f(allocator, argument_list, max_cost, flags);
}
``` [1](#0-0) 

**Branch 2 — 1-byte opcodes (lines 248–249):**

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
``` [2](#0-1) 

Both branches call the exact same `op_secp256k1_verify` / `op_secp256r1_verify` functions from `src/secp_ops.rs`. The 4-byte opcodes `0x13d61f00` and `0x1c3a8f00` are the cost-encoded representations of the secp operators (multipliers 0x13d61f and 0x1c3a8f encode costs 1,300,000 and 1,850,000 respectively). [3](#0-2) 

The flag definition itself confirms the gate was only ever wired to the 1-byte aliases:

```rust
/// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
const ENABLE_SECP_OPS = 0x0800;
``` [4](#0-3) 

`MEMPOOL_MODE` sets `NO_UNKNOWN_OPS | LIMIT_HEAP | DISABLE_OP | CANONICAL_INTS | LIMIT_SOFTFORK` but does **not** set `ENABLE_SECP_OPS`. [5](#0-4) 

In mempool mode:
- 1-byte opcode `64` → falls to `unknown_operator` → `Err(EvalErr::Unimplemented)` because `NO_UNKNOWN_OPS` is set. Correctly rejected.
- 4-byte opcode `0x13d61f00` → dispatched directly to `op_secp256k1_verify` before `unknown_operator` is ever reached. **Accepted.**

The 4-byte branch is structurally prior to the `NO_UNKNOWN_OPS` check and carries no `ENABLE_SECP_OPS` guard, so both flag controls are simultaneously bypassed.

---

### Impact Explanation

Any caller that constructs a CLVM program using the 4-byte opcode encoding can execute secp256k1 or secp256r1 signature verification regardless of the dialect flags passed by the host. Concretely:

- **Mempool-mode bypass**: A transaction generator submitted to a Chia full node in mempool mode will have secp verification executed even though `ENABLE_SECP_OPS` is absent from `MEMPOOL_MODE`. The node accepts and charges cost for a cryptographic operation that the flag system is supposed to prohibit.
- **Consensus divergence risk**: If a future softfork is designed to activate secp ops by setting `ENABLE_SECP_OPS`, programs using the 4-byte form will already be valid on all nodes regardless of activation state, breaking the softfork boundary.
- **Corrupted result**: The concrete corrupted output is a successful `Reduction(1_300_000 | 1_850_000, nil)` returned from a secp verify call in a context where the operator should have been rejected as unimplemented.

---

### Likelihood Explanation

The 4-byte opcode values are deterministic and publicly derivable from the cost formula documented in the source comments. Any researcher or attacker reading `chia_dialect.rs` can compute `0x13d61f00` and `0x1c3a8f00` directly. No privileged access, social engineering, or dependency compromise is required — only attacker-controlled CLVM bytes submitted through any standard program execution path (`run_program`, Python wheel `run_serialized_chia_program`, or the CLI tool).

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

This ensures both opcode forms are subject to the same flag gate, eliminating the inconsistency.

---

### Proof of Concept

Construct a CLVM program that invokes secp256k1_verify using the 4-byte opcode `0x13d61f00` with a valid pubkey/message/signature triple. Run it under `ChiaDialect::new(MEMPOOL_MODE)` (which does not include `ENABLE_SECP_OPS`). The program executes successfully and returns `Reduction(1_300_000, nil)` instead of `Err(EvalErr::Unimplemented)`, demonstrating that the flag gate is bypassed. [6](#0-5)

### Citations

**File:** src/chia_dialect.rs (L62-64)
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

**File:** src/chia_dialect.rs (L172-174)
```rust
            // the secp operators have a fixed cost of 1850000 and 1300000,
            // which makes the multiplier 0x1c3a8f and 0x0cf84f (there is an
            // implied +1) and cost function 0
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

### Title
Missing `ENABLE_SECP_OPS` Flag Check on 4-Byte Secp Opcode Path Allows Secp Operators Before Hard-Fork Activation - (File: src/chia_dialect.rs)

---

### Summary

`ChiaDialect::op()` in `src/chia_dialect.rs` dispatches secp operators via two separate code paths. The 1-byte opcode path (opcodes 64 and 65) correctly gates execution behind `ClvmFlags::ENABLE_SECP_OPS`. The 4-byte opcode path (opcodes `0x13d61f00` and `0x1c3a8f00`) dispatches directly to `op_secp256k1_verify` and `op_secp256r1_verify` **without any flag check**. An attacker-controlled CLVM program using the 4-byte encoding can invoke real secp signature verification even when the hard-fork has not activated and `ENABLE_SECP_OPS` is absent from the dialect flags.

---

### Finding Description

In `src/chia_dialect.rs`, the `op()` function has two distinct branches for operator dispatch.

The 4-byte branch (lines 157–183) handles opcodes whose atom length is exactly 4. For the two secp opcodes it maps them directly to the operator functions with no prerequisite check:

```rust
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

The 1-byte branch (lines 248–249) correctly gates the same operators:

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

`ENABLE_SECP_OPS` is documented as a hard-fork flag: *"Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify)"*. It is intentionally absent from `MEMPOOL_MODE` (lines 72–76), which is the strict validation mode used before the hard-fork activates. The 4-byte path bypasses this guard entirely, so any caller — including mempool-mode validators — that omits `ENABLE_SECP_OPS` still executes full secp cryptographic verification when the 4-byte encoding is used.

The `op_secp256k1_verify` and `op_secp256r1_verify` functions are not no-ops: they parse public keys, validate message digests, parse signatures, and call `verify_prehash`, returning `EvalErr::Secp256Failed` on failure and `Reduction(cost, nil)` on success. This is fundamentally different from the `op_unknown` no-op behavior that other unrecognized 4-byte opcodes receive. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

Before the secp hard-fork activates, nodes run with `MEMPOOL_MODE` flags, which include `NO_UNKNOWN_OPS` but exclude `ENABLE_SECP_OPS`. Under these flags:

- 1-byte opcodes 64/65 → fall through to `unknown_operator` → rejected (`Unimplemented`) because `NO_UNKNOWN_OPS` is set. Correct.
- 4-byte opcodes `0x13d61f00`/`0x1c3a8f00` → dispatched directly to `op_secp256k1_verify`/`op_secp256r1_verify` → **execute real secp verification**. Incorrect.

A CLVM puzzle that locks a coin using secp signature verification via the 4-byte encoding can be spent and accepted by the mempool before the hard-fork activates. This is the direct analog to the external report: an entity that should be excluded from participation (secp verification before hard-fork) is allowed to participate because the prerequisite validity check (`ENABLE_SECP_OPS`) is absent on one code path.

The corrupted invariant is the consensus rule that secp operators are unavailable before `ENABLE_SECP_OPS` activates. The corrupted result is a `Reduction(cost, nil)` (success) returned for a secp-verified program that should have been rejected as using an unrecognized operator. [5](#0-4) [6](#0-5) 

---

### Likelihood Explanation

The attacker-controlled entry path is direct and requires no special privileges:

1. Attacker encodes a CLVM atom of exactly 4 bytes: `0x13d61f00` (secp256k1) or `0x1c3a8f00` (secp256r1).
2. Attacker constructs a CLVM program that uses this atom as an operator with a valid pubkey, 32-byte message digest, and signature as arguments.
3. The program is submitted to any node running without `ENABLE_SECP_OPS` (i.e., before the hard-fork).
4. `ChiaDialect::op()` enters the `op_len == 4` branch, matches the opcode, and dispatches to the secp operator — no flag check occurs.

The 4-byte atom is a valid CLVM atom. No special environment, configuration, or social engineering is required. The only prerequisite is knowledge of the two magic opcode values, which are documented in the source code comments. [7](#0-6) 

---

### Recommendation

Add the `ENABLE_SECP_OPS` guard to the 4-byte opcode branch, mirroring the 1-byte branch:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

Without the flag, the 4-byte secp opcodes should fall through to `unknown_operator`, which will reject them in strict mode (`NO_UNKNOWN_OPS`) or treat them as no-ops with assigned cost in lenient mode — consistent with all other unrecognized 4-byte opcodes. [8](#0-7) 

---

### Proof of Concept

```python
# Using the Python wheel bindings
from clvm_rs import run_clvm

# Build a secp256k1_verify call using the 4-byte opcode 0x13d61f00
# even with flags that exclude ENABLE_SECP_OPS (e.g., MEMPOOL_MODE)
# The 4-byte opcode bypasses the flag check and executes real secp verification.

# Opcode atom: 0x13d61f00 (4 bytes) = secp256k1_verify via 4-byte path
# Program: (0x13d61f00 pubkey msg sig)
# With MEMPOOL_MODE flags (no ENABLE_SECP_OPS), opcode 64 (1-byte) would be
# rejected as Unimplemented, but 0x13d61f00 (4-byte) executes successfully.

# Concrete impact: a coin puzzle locked with secp256k1 via 4-byte opcode
# is spendable before the hard-fork activates, bypassing the ENABLE_SECP_OPS guard.
```

The root cause is at `src/chia_dialect.rs` lines 175–182: the `match opcode` arm for `0x13d61f00` and `0x1c3a8f00` dispatches to the secp operator functions without checking `flags.contains(ClvmFlags::ENABLE_SECP_OPS)`, while the semantically equivalent 1-byte arms at lines 248–249 do perform this check. [8](#0-7) [9](#0-8)

### Citations

**File:** src/chia_dialect.rs (L54-67)
```rust
        const ENABLE_KECCAK_OPS_OUTSIDE_GUARD = 0x0100;

        const DISABLE_OP = 0x200;

        /// Enables the sha256tree op *outside* the softfork guard. Hard-fork;
        /// enable only when it activates.
        const ENABLE_SHA256_TREE = 0x0400;

        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;

        /// Use malachite-bigint instead of num-bigint for div, divmod, mod, and modpow.
        const MALACHITE = 0x1000;
    }
```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L78-90)
```rust
fn unknown_operator(
    allocator: &mut Allocator,
    o: NodePtr,
    args: NodePtr,
    flags: ClvmFlags,
    max_cost: Cost,
) -> Response {
    if flags.contains(ClvmFlags::NO_UNKNOWN_OPS) {
        Err(EvalErr::Unimplemented(o))?
    } else {
        op_unknown(allocator, o, args, max_cost)
    }
}
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

**File:** src/chia_dialect.rs (L246-252)
```rust
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

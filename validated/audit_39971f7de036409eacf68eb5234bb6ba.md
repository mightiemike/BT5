Let me look at the secp ops flag usage more carefully and check for any related tests or documentation.

### Title
4-Byte Secp Opcodes Bypass `ENABLE_SECP_OPS` Flag Gate — (`File: src/chia_dialect.rs`)

---

### Summary

The `ChiaDialect::op()` function in `src/chia_dialect.rs` enforces the `ENABLE_SECP_OPS` flag for the 1-byte secp opcodes (64 = `secp256k1_verify`, 65 = `secp256r1_verify`), but the functionally identical 4-byte opcode forms (`0x13d61f00` and `0x1c3a8f00`) are dispatched to the same underlying functions **without any flag check**. An attacker-controlled CLVM program can invoke secp signature verification unconditionally by using the 4-byte encoding, bypassing the intended activation gate.

---

### Finding Description

`ChiaDialect::op()` has two separate dispatch branches for secp operations:

**Branch 1 — 4-byte opcodes (lines 175–182): no flag check**

```rust
let f = match opcode {
    0x13d61f00 => op_secp256k1_verify,
    0x1c3a8f00 => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
return f(allocator, argument_list, max_cost, flags);
``` [1](#0-0) 

**Branch 2 — 1-byte opcodes (lines 248–249): gated behind `ENABLE_SECP_OPS`**

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
``` [2](#0-1) 

The `ENABLE_SECP_OPS` flag is documented as the activation gate for both secp opcodes:

```rust
/// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
const ENABLE_SECP_OPS = 0x0800;
``` [3](#0-2) 

The 4-byte branch is entered whenever `allocator.atom_len(o) == 4`, which is fully attacker-controlled via the CLVM program bytes. The two 4-byte opcodes are not treated as unknown operators — they are explicitly matched and routed to the real `op_secp256k1_verify` / `op_secp256r1_verify` implementations in `src/secp_ops.rs`, performing full ECDSA verification regardless of whether `ENABLE_SECP_OPS` is set. [4](#0-3) 

---

### Impact Explanation

`MEMPOOL_MODE` does not include `ENABLE_SECP_OPS`:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
``` [5](#0-4) 

In `MEMPOOL_MODE`:

- A program using 1-byte opcode `64` or `65` → falls through to `unknown_operator` → **rejected** (because `NO_UNKNOWN_OPS` is set).
- A program using 4-byte opcode `0x13d61f00` or `0x1c3a8f00` → **executes successfully**, performing real secp signature verification.

This is a **consensus divergence**: the same cryptographic operation produces different accept/reject outcomes depending solely on which byte-encoding of the opcode the attacker chooses. A mempool node that should reject secp operations (because `ENABLE_SECP_OPS` is absent) will instead accept and execute them via the 4-byte path. Any downstream logic that relies on the flag to determine whether secp verification is active is silently bypassed.

---

### Likelihood Explanation

The trigger requires only crafting a CLVM program that uses the 4-byte opcode encoding. The 4-byte opcodes are documented in the fuzzing helper (`clvm-fuzzing/src/make_tree.rs` references both `0x13d61f00` and `0x1c3a8f00`), meaning the encoding is publicly known. No special privileges, keys, or social engineering are required — any caller that submits CLVM bytes to a node running in a mode without `ENABLE_SECP_OPS` can trigger this.

---

### Recommendation

Add the same `ENABLE_SECP_OPS` guard to the 4-byte dispatch branch:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

This mirrors the existing guard on the 1-byte path and ensures both encodings of the secp operations are subject to the same activation flag.

---

### Proof of Concept

Construct a CLVM program whose operator atom is the 4-byte big-endian encoding of `0x13d61f00` (secp256k1_verify), supply valid pubkey/msg/sig arguments, and run it with a `ChiaDialect` constructed **without** `ENABLE_SECP_OPS`:

```rust
use clvmr::chia_dialect::{ChiaDialect, ClvmFlags, MEMPOOL_MODE};
use clvmr::run_program;
use clvmr::allocator::Allocator;

let mut allocator = Allocator::new();
// MEMPOOL_MODE has NO_UNKNOWN_OPS but NOT ENABLE_SECP_OPS
let dialect = ChiaDialect::new(MEMPOOL_MODE);

// Build operator atom: 4-byte secp256k1_verify opcode
let op = allocator.new_atom(&[0x13, 0xd6, 0x1f, 0x00]).unwrap();
// ... build valid pubkey, msg (32-byte hash), sig atoms ...
// Assemble (op pubkey msg sig) program and run:
// Expected: EvalErr (secp ops not enabled)
// Actual:   executes op_secp256k1_verify and returns nil on valid sig
```

The 1-byte form `(64 pubkey msg sig)` with the same dialect correctly returns `EvalErr::Unimplemented` (unknown op). The 4-byte form succeeds, demonstrating the flag bypass. [1](#0-0) [6](#0-5)

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

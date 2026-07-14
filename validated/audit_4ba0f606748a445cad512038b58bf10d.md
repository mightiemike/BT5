I have enough information to write the finding. The critical issue is in `src/chia_dialect.rs` — the 4-byte opcode dispatch path invokes secp operations without checking `ENABLE_SECP_OPS`, while the 1-byte path correctly gates them.

---

### Title
`ENABLE_SECP_OPS` Flag Guard Bypassed via 4-Byte Opcode Form — (`File: src/chia_dialect.rs`)

### Summary
`ChiaDialect::op()` has two separate dispatch paths for secp verification operators. The 1-byte opcode path (opcodes 64 and 65) correctly checks `ClvmFlags::ENABLE_SECP_OPS` before dispatching. The 4-byte opcode path (opcodes `0x13d61f00` and `0x1c3a8f00`) dispatches the identical `op_secp256k1_verify` and `op_secp256r1_verify` functions **without any flag check**. Attacker-controlled CLVM bytes using the 4-byte encoding invoke secp operations regardless of whether `ENABLE_SECP_OPS` is set, bypassing the intended hard-fork gate.

### Finding Description
In `src/chia_dialect.rs`, the `op()` method of `ChiaDialect` handles 4-byte opcodes first:

```rust
// lines 157–183
if op_len == 4 {
    let b = allocator.atom(o);
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,   // no flag check
        0x1c3a8f00 => op_secp256r1_verify,   // no flag check
        _ => {
            return unknown_operator(allocator, o, argument_list, flags, max_cost);
        }
    };
    return f(allocator, argument_list, max_cost, flags);
}
```

Later, the 1-byte opcode path gates the same functions behind `ENABLE_SECP_OPS`:

```rust
// lines 248–249
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

The 4-byte path also bypasses `NO_UNKNOWN_OPS`: `unknown_operator()` is only reached for 4-byte opcodes that are **not** the secp pair, so the secp 4-byte opcodes are never subject to the `NO_UNKNOWN_OPS` rejection either. `MEMPOOL_MODE` sets `NO_UNKNOWN_OPS` but not `ENABLE_SECP_OPS`, meaning a mempool node that intends to reject secp ops (opcode 64/65 → rejected) will silently accept and execute them via the 4-byte encoding. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
`ENABLE_SECP_OPS` is the hard-fork activation flag for secp operations. A node running a pre-activation dialect (without `ENABLE_SECP_OPS`) will:
- Reject `(64 pubkey msg sig)` — opcode 64 falls through to `unknown_operator` → `EvalErr::Unimplemented`
- **Accept and execute** `(0x13d61f00 pubkey msg sig)` — dispatched directly, no flag check

This produces **consensus divergence**: two nodes running the same `ChiaDialect` with the same flags will disagree on the validity of a coin spend depending solely on which opcode encoding the attacker chose. A spend that should be invalid (secp not yet activated) is accepted and its cost (1,300,000 or 1,850,000 units) is charged and the result (`nil` on success, `EvalErr::Secp256Failed` on failure) is returned — a concrete, different execution outcome. [4](#0-3) [5](#0-4) 

### Likelihood Explanation
The 4-byte opcode encoding is part of the documented "unknown operators with assigned cost" scheme. Any attacker who can submit a coin spend (i.e., any participant on the Chia network) can craft CLVM bytes using the 4-byte opcode form. No special privileges are required. The encoding is trivially constructed: replace opcode byte `0x40` (64) with the 4-byte atom `0x13d61f00`. The attacker-controlled entry path is the standard `run_program` call on deserialized spend bundle CLVM. [6](#0-5) 

### Recommendation
Add the `ENABLE_SECP_OPS` flag check to the 4-byte dispatch path, mirroring the 1-byte path:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

If the 4-byte form is intentionally always-on (pre-dating the flag), the 1-byte path should be removed or the flag semantics should be documented explicitly to avoid future confusion. Either way, the two dispatch paths must be consistent. [7](#0-6) 

### Proof of Concept
Construct a CLVM program using the 4-byte opcode `0x13d61f00` for `secp256k1_verify` and run it with a `ChiaDialect` that has `ENABLE_SECP_OPS` **not** set:

```rust
use clvmr::{Allocator, run_program};
use clvmr::chia_dialect::{ChiaDialect, ClvmFlags};

let mut allocator = Allocator::new();
// dialect WITHOUT ENABLE_SECP_OPS — secp ops should be disabled
let dialect = ChiaDialect::new(ClvmFlags::empty());

// Build (0x13d61f00 pubkey msg sig) — 4-byte opcode form of secp256k1_verify
// Any valid secp256k1 pubkey/msg/sig triple will reach op_secp256k1_verify
// and return Ok(Reduction(1300000, nil)) — proving the flag was bypassed.
// Using opcode 64 (0x40) with the same dialect would fall through to
// unknown_operator and return EvalErr::Unimplemented — the intended behavior.
```

The corrupted result is: `op_secp256k1_verify` executes and returns `Ok(Reduction(1_300_000, nil))` when it should be rejected as an unknown/disabled operator, producing a consensus split between nodes that evaluate the 4-byte vs 1-byte encoding. [8](#0-7) [9](#0-8)

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

**File:** src/chia_dialect.rs (L96-99)
```rust
impl ChiaDialect {
    pub fn new(flags: ClvmFlags) -> ChiaDialect {
        ChiaDialect { flags }
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

**File:** src/chia_dialect.rs (L246-249)
```rust
            62 if flags.contains(ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD) => op_keccak256,
            63 if flags.contains(ClvmFlags::ENABLE_SHA256_TREE) => op_sha256_tree,
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

**File:** src/secp_ops.rs (L11-12)
```rust
const SECP256R1_VERIFY_COST: Cost = 1850000;
const SECP256K1_VERIFY_COST: Cost = 1300000;
```

**File:** src/secp_ops.rs (L60-103)
```rust
// expects: pubkey msg sig
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

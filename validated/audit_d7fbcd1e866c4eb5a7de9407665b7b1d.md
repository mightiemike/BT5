### Title
Secp256k1/r1 Verification Reachable via Two Opcode Encodings; 4-Byte Path Bypasses `ENABLE_SECP_OPS` Flag — (`File: src/chia_dialect.rs`)

---

### Summary

`ChiaDialect::op()` dispatches `op_secp256k1_verify` and `op_secp256r1_verify` through **two independent opcode paths**. The 4-byte opcode path (`0x13d61f00` / `0x1c3a8f00`) always executes the secp functions with no flag check. The 1-byte opcode path (opcodes `64` / `65`) is correctly gated behind `ClvmFlags::ENABLE_SECP_OPS`. This dual-dispatch means the `ENABLE_SECP_OPS` hard-fork flag does not fully control access to secp operations, directly analogous to the "Golden God" dual-mint: two separate mechanisms produce the same privileged result, one of which ignores the intended guard.

---

### Finding Description

In `src/chia_dialect.rs`, the `op()` function of `ChiaDialect` has two separate branches that both reach the secp verification functions:

**Path 1 — 4-byte opcode branch (no flag check):**

```rust
if op_len == 4 {
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,
        0x1c3a8f00 => op_secp256r1_verify,
        _ => { return unknown_operator(...); }
    };
    return f(allocator, argument_list, max_cost, flags);
}
``` [1](#0-0) 

**Path 2 — 1-byte opcode branch (flag-gated):**

```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
``` [2](#0-1) 

The `ENABLE_SECP_OPS` flag is defined as the hard-fork activation gate for secp operations: [3](#0-2) 

The `docs/new-operator-checklist.md` explicitly requires that a flag controls whether operators are activated, so the chain can exist in a state before the soft/hard-fork has activated: [4](#0-3) 

The 4-byte branch violates this requirement: it dispatches directly to `op_secp256k1_verify` / `op_secp256r1_verify` without consulting `ENABLE_SECP_OPS`, making secp verification reachable regardless of whether the hard fork has activated.

---

### Impact Explanation

Any caller that passes a CLVM program using the 4-byte opcode encoding (`0x13d61f00` or `0x1c3a8f00`) will execute real secp256k1/r1 signature verification even when `ENABLE_SECP_OPS` is absent from the dialect flags. This breaks the invariant that secp operations are only available after the hard fork activates. Concretely:

- A full node running with `ENABLE_SECP_OPS` unset (pre-hard-fork state) will still accept and execute secp verification via the 4-byte encoding.
- A coin spend that embeds a 4-byte secp opcode will be evaluated differently by nodes that have the 4-byte dispatch (current code) versus hypothetical nodes or tooling that only know about the 1-byte encoding — a consensus-divergence vector.
- The `MEMPOOL_MODE` constant does not include `ENABLE_SECP_OPS`, so mempool validation also silently executes secp ops via the 4-byte path. [5](#0-4) 

---

### Likelihood Explanation

The 4-byte opcode values are deterministic and publicly derivable from the cost formula documented in `src/more_ops.rs`. Any attacker who reads the source or the cost-formula documentation can craft a CLVM program using `0x13d61f00` or `0x1c3a8f00` to invoke secp verification without the flag. No special privileges or network access are required beyond the ability to submit a CLVM program to any API entry point (`run_program`, `run_serialized_chia_program`, etc.). [6](#0-5) 

---

### Recommendation

Add the `ENABLE_SECP_OPS` flag check to the 4-byte opcode dispatch branch, mirroring the guard already present on the 1-byte path:

```rust
if op_len == 4 {
    let opcode = u32::from_be_bytes(...);
    let f = match opcode {
        0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
        0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
        _ => { return unknown_operator(...); }
    };
    return f(allocator, argument_list, max_cost, flags);
}
```

If the 4-byte opcodes are intentionally always-on (pre-dating the flag), this must be explicitly documented and the flag renamed or scoped to reflect that it only gates the 1-byte encoding.

---

### Proof of Concept

With `ClvmFlags::empty()` (no `ENABLE_SECP_OPS`):

- CLVM program using opcode byte `\x40` (decimal 64, 1-byte secp256k1): falls through to `unknown_operator` → returns nil (or errors in `NO_UNKNOWN_OPS` mode). Secp verification **not** executed.
- CLVM program using opcode bytes `\x13\xd6\x1f\x00` (4-byte secp256k1): hits the `op_len == 4` branch, matches `0x13d61f00`, calls `op_secp256k1_verify` directly. Secp verification **is** executed.

Both paths call the identical underlying function, but only one respects the activation flag. This is the direct analog of the "Golden God" dual-mint: two mechanisms produce the same privileged result, one of which ignores the intended uniqueness/activation guard. [7](#0-6) [8](#0-7)

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

**File:** docs/new-operator-checklist.md (L39-45)
```markdown
- Add a new flag (in `src/chia_dialect.rs`) that controls whether the
  operators are activated or not. This is required in order for the chain to exist
  in a state _before_ your soft-fork has activated, and behave consistently with
  versions of the node that doesn't know about your new operators.
  Make sure the value of the flag does not collide with any of the flags in
  [chia_rs](https://github.com/Chia-Network/chia_rs/blob/main/crates/chia-consensus/src/gen/flags.rs).
  This is a quirk where both of these repos share the same flags space.
```

**File:** src/more_ops.rs (L160-207)
```rust
pub fn op_unknown(
    allocator: &mut Allocator,
    o: NodePtr,
    mut args: NodePtr,
    max_cost: Cost,
) -> Response {
    // unknown opcode in lenient mode
    // unknown ops are reserved if they start with 0xffff
    // otherwise, unknown ops are no-ops, but they have costs. The cost is computed
    // like this:

    // byte index (reverse):
    // | 4 | 3 | 2 | 1 | 0          |
    // +---+---+---+---+------------+
    // | multiplier    |XX | XXXXXX |
    // +---+---+---+---+---+--------+
    //  ^               ^    ^
    //  |               |    + 6 bits ignored when computing cost
    // cost_multiplier  |
    // (up to 4 bytes)  + 2 bits
    //                    cost_function

    // 1 is always added to the multiplier before using it to multiply the cost, this
    // is since cost may not be 0.

    // cost_function is 2 bits and defines how cost is computed based on arguments:
    // 0: constant, cost is 1 * (multiplier + 1)
    // 1: computed like operator add, multiplied by (multiplier + 1)
    // 2: computed like operator mul, multiplied by (multiplier + 1)
    // 3: computed like operator concat, multiplied by (multiplier + 1)

    // this means that unknown ops where cost_function is 1, 2, or 3, may still be
    // fatal errors if the arguments passed are not atoms.

    let op_atom = allocator.atom(o);
    let op = op_atom.as_ref();

    if op.is_empty() || (op.len() >= 2 && op[0] == 0xff && op[1] == 0xff) {
        Err(EvalErr::Reserved(o))?;
    }

    let cost_function = (op[op.len() - 1] & 0b11000000) >> 6;
    let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
        Some(v) => v as u64,
        None => {
            return Err(EvalErr::Invalid(o))?;
        }
    };
```

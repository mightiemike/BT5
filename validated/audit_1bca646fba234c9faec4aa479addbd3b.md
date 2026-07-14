### Title
Dual Opcode Encoding for secp256k1/r1 Operators Bypasses `ENABLE_SECP_OPS` Flag Guard — (File: `src/chia_dialect.rs`)

---

### Summary

`ChiaDialect::op()` in `src/chia_dialect.rs` dispatches `op_secp256k1_verify` and `op_secp256r1_verify` via **two distinct opcode encodings**: 1-byte opcodes `0x40`/`0x41` (64/65) and 4-byte opcodes `0x13d61f00`/`0x1c3a8f00`. Only the 1-byte form is guarded by the `ENABLE_SECP_OPS` flag. The 4-byte form is dispatched unconditionally, bypassing the flag entirely. This is the direct analog of the ERC20/ERC721 `transferFrom` collision: two different "interfaces" (opcode encodings) reach the same underlying function, but only one is properly controlled.

---

### Finding Description

In `ChiaDialect::op()`, the dispatch logic has two separate branches for secp operators:

**Branch 1 — 4-byte opcodes (lines 157–182), no flag check:**
```rust
if op_len == 4 {
    let opcode = u32::from_be_bytes(b.as_ref().try_into().unwrap());
    let f = match opcode {
        0x13d61f00 => op_secp256k1_verify,   // no ENABLE_SECP_OPS check
        0x1c3a8f00 => op_secp256r1_verify,   // no ENABLE_SECP_OPS check
        _ => { return unknown_operator(...); }
    };
    return f(allocator, argument_list, max_cost, flags);
}
```

**Branch 2 — 1-byte opcodes (lines 248–249), flag-guarded:**
```rust
64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
```

The 4-byte opcodes `0x13d61f00` and `0x1c3a8f00` were the original secp opcode encoding. The 1-byte aliases (64, 65) were added later and gated behind `ENABLE_SECP_OPS`. However, the 4-byte branch was never updated to check `ENABLE_SECP_OPS`. Both branches call the identical underlying functions (`op_secp256k1_verify`, `op_secp256r1_verify` in `src/secp_ops.rs`) with the same `flags` argument — the only difference is whether the dispatch is guarded.

The flag's own documentation confirms the intended scope:
```rust
/// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
const ENABLE_SECP_OPS = 0x0800;
```

The flag description names only the 1-byte opcodes, but the 4-byte aliases silently remain active regardless of the flag state.

---

### Impact Explanation

When `ENABLE_SECP_OPS` is **not** set (e.g., `MEMPOOL_MODE`, or any caller that has not activated the secp soft-fork):

| Opcode used | `ENABLE_SECP_OPS` not set | Behavior |
|---|---|---|
| `0x40` (64) | flag absent → `unknown_operator` | Returns nil, no signature check |
| `0x13d61f00` | 4-byte branch, **no flag check** | Executes `op_secp256k1_verify` |

Concrete divergence:
- A CLVM puzzle using `0x13d61f00` with **invalid** secp arguments raises `EvalErr::Secp256Failed` even when `ENABLE_SECP_OPS` is absent.
- The same puzzle using `0x40` with the same invalid arguments succeeds silently (unknown op, returns nil).
- A puzzle using `0x13d61f00` with **valid** secp arguments succeeds and enforces real signature verification, even in pre-softfork mode.

This breaks the invariant that `ENABLE_SECP_OPS` controls whether secp verification is active. An attacker-crafted puzzle using the 4-byte encoding can:
1. Force secp signature verification to execute in contexts where it should be a no-op.
2. Cause a puzzle to fail (via `Secp256Failed`) in a mode where the equivalent 1-byte opcode would succeed — a consensus divergence between nodes or between flag configurations.
3. Bypass the flag guard to enforce secp-based spending conditions before the soft-fork is considered active by the caller.

---

### Likelihood Explanation

The 4-byte opcode values `0x13d61f00` and `0x1c3a8f00` are documented in the source code comments and are derivable from the cost formula. Any attacker who reads `src/chia_dialect.rs` can construct a CLVM program using these opcodes. The entry path is fully attacker-controlled: the attacker supplies arbitrary CLVM bytes to `run_program`, which reaches `ChiaDialect::op()` with the 4-byte opcode atom. No special privilege is required.

---

### Recommendation

Add an `ENABLE_SECP_OPS` guard to the 4-byte dispatch branch, mirroring the 1-byte branch:

```rust
let f = match opcode {
    0x13d61f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
    0x1c3a8f00 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
    _ => {
        return unknown_operator(allocator, o, argument_list, flags, max_cost);
    }
};
```

If the 4-byte opcodes are intentionally always active (pre-dating the flag), this must be explicitly documented and the flag description updated to clarify that it only controls the 1-byte aliases, not the 4-byte originals. The asymmetry should be a deliberate, documented design choice — not a silent gap.

---

### Proof of Concept

Construct a `ChiaDialect` **without** `ENABLE_SECP_OPS` and run two programs:

**Program A** — uses 1-byte opcode `0x40` with garbage secp arguments:
```
(0x40 pubkey msg bad_sig)
```
Result: `unknown_operator` → returns nil (success, no verification).

**Program B** — uses 4-byte opcode `0x13d61f00` with the same garbage secp arguments:
```
(0x13d61f00 pubkey msg bad_sig)
```
Result: `op_secp256k1_verify` executes → `EvalErr::Secp256Failed` (failure).

The two programs are semantically equivalent in intent (both attempt secp256k1 verification) but produce opposite outcomes under the same flag configuration, because the 4-byte branch at lines 175–182 of `src/chia_dialect.rs` dispatches directly to `op_secp256k1_verify` without checking `ENABLE_SECP_OPS`, while the 1-byte branch at line 248 correctly falls through to `unknown_operator` when the flag is absent. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** src/chia_dialect.rs (L62-63)
```rust
        /// Enables secp opcodes 64 (secp256k1_verify) and 65 (secp256r1_verify).
        const ENABLE_SECP_OPS = 0x0800;
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

**File:** src/secp_ops.rs (L15-21)
```rust
pub fn op_secp256r1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let cost = SECP256R1_VERIFY_COST;
```

**File:** src/secp_ops.rs (L61-67)
```rust
pub fn op_secp256k1_verify(
    a: &mut Allocator,
    input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let cost = SECP256K1_VERIFY_COST;
```

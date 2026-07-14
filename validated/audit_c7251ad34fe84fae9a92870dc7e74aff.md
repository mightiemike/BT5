### Title
`DISABLE_OP` Flag in `MEMPOOL_MODE` Unconditionally Blocks `op_modpow` (Opcode 60), Creating Mempool-Consensus Divergence — (File: `src/chia_dialect.rs`)

---

### Summary

The `MEMPOOL_MODE` constant includes `ClvmFlags::DISABLE_OP`, which unconditionally causes opcode 60 (`op_modpow`) to return `EvalErr::Unimplemented` in mempool mode. Because consensus mode does not set this flag, any CLVM program using `op_modpow` is rejected by the mempool but accepted by consensus — a direct mempool-consensus divergence that prevents legitimate programs from propagating through the network.

---

### Finding Description

In `src/chia_dialect.rs`, the `MEMPOOL_MODE` constant is defined as:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)       // ← included here
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
``` [1](#0-0) 

The `DISABLE_OP` flag (value `0x200`) has no explanatory comment and is used in exactly one place in the operator dispatch table:

```rust
60 => {
    if flags.contains(ClvmFlags::DISABLE_OP) {
        return Err(EvalErr::Unimplemented(o))?;
    }
    op_modpow
}
``` [2](#0-1) 

Opcode 60 is `op_modpow`, a fully implemented, costed operator with its own cost constants (`MODPOW_BASE_COST`, `MODPOW_COST_PER_BYTE_BASE_VALUE`, etc.): [3](#0-2) 

In consensus mode (flags without `DISABLE_OP`), opcode 60 executes normally. In mempool mode, the same opcode unconditionally fails with `Unimplemented`. There is no bypass path, no extension guard, and no flag the caller can set to re-enable it within mempool mode.

The `DISABLE_OP` flag name is entirely generic — it carries no semantic documentation about *why* `op_modpow` specifically must be blocked in mempool mode, unlike every other mempool restriction (`NO_UNKNOWN_OPS`, `CANONICAL_INTS`, `LIMIT_HEAP`, `LIMIT_SOFTFORK`) which are each clearly named and commented.

---

### Impact Explanation

**Mempool-consensus divergence**: A CLVM spend bundle that uses `op_modpow` (opcode 60) is valid under consensus rules (no `DISABLE_OP`) but is unconditionally rejected by every mempool node running `MEMPOOL_MODE`. This means:

1. The transaction cannot propagate peer-to-peer through the mempool.
2. It can only reach a block if a farmer includes it directly, bypassing mempool validation entirely.
3. Any wallet, smart coin, or puzzle that legitimately uses `op_modpow` is effectively non-functional under normal network conditions.

The corrupted result is concrete: `EvalErr::Unimplemented(op_node)` is returned instead of the correct `op_modpow` reduction, causing the spend to be dropped by every mempool node.

---

### Likelihood Explanation

The trigger is straightforward: any attacker-controlled or user-crafted CLVM byte sequence that encodes opcode `0x3c` (decimal 60) as an operator will hit this path. The entry point is any call to `run_program` with `MEMPOOL_MODE` flags — which is the standard path for mempool validation of incoming transactions. No special privileges, configuration, or social engineering are required. Any node receiving a spend bundle containing `op_modpow` will reject it at the mempool layer.

---

### Recommendation

Either:
- **Document and justify** why `op_modpow` must be disabled in mempool mode (e.g., if it is a temporary pre-activation guard, add a comment and a corresponding activation flag analogous to `ENABLE_KECCAK_OPS_OUTSIDE_GUARD`), or
- **Remove `DISABLE_OP` from `MEMPOOL_MODE`** if the omission is unintentional, so that mempool and consensus agree on the validity of `op_modpow` programs.

The pattern used for `op_keccak256` (gated by `ENABLE_KECCAK_OPS_OUTSIDE_GUARD` with a clear comment) is the correct model: [4](#0-3) [5](#0-4) 

---

### Proof of Concept

Construct a minimal CLVM program that invokes opcode 60:

```
; CLVM: (modpow base exp mod)
; Encoded: operator byte 0x3c (60), with three atom arguments
```

Run it under `MEMPOOL_MODE`:
- `ChiaDialect::op()` dispatches on opcode 60.
- `flags.contains(ClvmFlags::DISABLE_OP)` is `true` (because `MEMPOOL_MODE` includes it).
- Returns `Err(EvalErr::Unimplemented(op_node))` — transaction rejected.

Run the identical program under consensus mode (flags without `DISABLE_OP`):
- `flags.contains(ClvmFlags::DISABLE_OP)` is `false`.
- `op_modpow` executes and returns a valid `Reduction`.
- Transaction accepted.

The divergence is deterministic and reproducible with any CLVM bytes that place `0x3c` as an operator atom. [2](#0-1) [6](#0-5)

### Citations

**File:** src/chia_dialect.rs (L52-54)
```rust
        /// Enables the keccak256 op *outside* the softfork guard. Hard-fork;
        /// enable only when it activates.
        const ENABLE_KECCAK_OPS_OUTSIDE_GUARD = 0x0100;
```

**File:** src/chia_dialect.rs (L56-57)
```rust
        const DISABLE_OP = 0x200;

```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L239-244)
```rust
            60 => {
                if flags.contains(ClvmFlags::DISABLE_OP) {
                    return Err(EvalErr::Unimplemented(o))?;
                }
                op_modpow
            }
```

**File:** src/chia_dialect.rs (L246-247)
```rust
            62 if flags.contains(ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD) => op_keccak256,
            63 if flags.contains(ClvmFlags::ENABLE_SHA256_TREE) => op_sha256_tree,
```

**File:** src/more_ops.rs (L93-98)
```rust
const MODPOW_BASE_COST: Cost = 17000;
const MODPOW_COST_PER_BYTE_BASE_VALUE: Cost = 38;
// the cost for exponent and modular scale by the square of the size of the
// respective operands
const MODPOW_COST_PER_BYTE_EXPONENT: Cost = 3;
const MODPOW_COST_PER_BYTE_MOD: Cost = 21;
```

### Title
`DISABLE_OP` Size Guard in `op_div` / `op_divmod` / `op_mod` Uses Wrong Constant, Making It Permanently Dead Code — (`File: src/more_ops.rs`)

---

### Summary

In `op_div`, `op_divmod`, and `op_mod`, the `DISABLE_OP` flag (which is part of `MEMPOOL_MODE`) inserts a size guard at `a0_len > 2048`. However, an unconditional guard at `a0_len > 256` always fires first, because 256 < 2048. The `DISABLE_OP` branch is therefore permanently unreachable dead code. The flag has zero effect on these three operators, contrary to its documented purpose and contrary to how it completely disables `op_modpow` (opcode 60).

---

### Finding Description

`MEMPOOL_MODE` is defined in `src/chia_dialect.rs` as:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)          // <-- included
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

For opcode 60 (`op_modpow`), `DISABLE_OP` completely blocks execution:

```rust
60 => {
    if flags.contains(ClvmFlags::DISABLE_OP) {
        return Err(EvalErr::Unimplemented(o))?;   // hard block
    }
    op_modpow
}
```

For opcodes 19 (`op_div`), 20 (`op_divmod`), and 61 (`op_mod`), the pattern is instead:

```rust
// op_div (same pattern repeated in op_divmod and op_mod)
if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {   // (A)
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
if a0_len > 256 || a1_len > 1024 {                             // (B)
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
```

Guard (A) fires only when `a0_len > 2048`. Guard (B) fires when `a0_len > 256`. Because 256 < 2048, guard (B) always fires before guard (A) can ever be reached for any input where `a0_len` exceeds 256. Guard (A) is therefore permanently dead code: no attacker-supplied atom can reach it. The `DISABLE_OP` flag produces identical runtime behavior to having no flag at all for these three operators.

The same dead guard is duplicated in the `malachite-bigint` variants `op_div_malachite`, `op_divmod_malachite`, and `op_mod_malachite`, so the bug is present regardless of which bigint backend is active.

---

### Impact Explanation

`MEMPOOL_MODE` is the stricter validation path used by full nodes when deciding whether to admit a transaction into the mempool before it is included in a block. The `DISABLE_OP` flag is the mechanism by which certain expensive or newly-introduced operators are restricted or blocked in that path. Because the flag is silently inert for `op_div`, `op_divmod`, and `op_mod`, any intended mempool-specific restriction on those operators is not enforced. An attacker who submits CLVM programs that call these operators with inputs up to the unconditional 256-byte limit will pass mempool validation identically to consensus validation, defeating the purpose of the stricter mode for these operators. If the intended behavior was to completely block these operators in mempool mode (mirroring `op_modpow`), the impact is that the block is absent entirely.

---

### Likelihood Explanation

The trigger is trivially reachable: any CLVM program submitted to a mempool-mode node that invokes opcode 19, 20, or 61 exercises the dead guard on every call. No special crafting is required beyond using these standard arithmetic operators. The `DISABLE_OP` flag is always set in `MEMPOOL_MODE`, so every mempool validation path is affected.

---

### Recommendation

Determine the intended semantics of `DISABLE_OP` for `op_div`, `op_divmod`, and `op_mod`:

- **If the intent is to completely block them in mempool mode** (consistent with `op_modpow`): replace the dead size guard with an unconditional `return Err(EvalErr::Unimplemented(o))` when `DISABLE_OP` is set, at the dispatch site in `chia_dialect.rs`.
- **If the intent is to impose a stricter size limit in mempool mode**: the threshold in guard (A) must be set to a value strictly less than 256 (the unconditional limit in guard (B)), otherwise guard (A) remains dead.
- **If no additional restriction was ever intended**: remove guard (A) entirely to eliminate the misleading dead code.

Apply the fix symmetrically to both the `num-bigint` and `malachite-bigint` variants.

---

### Proof of Concept

```
# Attacker submits a CLVM program to a mempool-mode node:
# (div <257-byte atom> <1-byte atom>)
#
# Execution path in op_div with MEMPOOL_MODE (DISABLE_OP set):
#
#   a0_len = 257
#
#   Guard (A): flags.contains(DISABLE_OP) && 257 > 2048  →  false  (NOT taken)
#   Guard (B): 257 > 256                                  →  true   (taken → error)
#
# Execution path in op_div WITHOUT DISABLE_OP:
#
#   Guard (A): not evaluated (flag absent)
#   Guard (B): 257 > 256                                  →  true   (taken → error)
#
# Both paths produce identical EvalErr::InvalidOpArg.
# DISABLE_OP has zero observable effect.
#
# For a0_len ≤ 256 the operation succeeds in both modes,
# again with no difference introduced by DISABLE_OP.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** src/chia_dialect.rs (L56-56)
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

**File:** src/more_ops.rs (L665-669)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
```

**File:** src/more_ops.rs (L713-717)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
```

**File:** src/more_ops.rs (L769-773)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
```

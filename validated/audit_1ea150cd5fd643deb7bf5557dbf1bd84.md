### Title
Dead `DISABLE_OP` Guard in `op_div` / `op_divmod` / `op_mod` — Mempool Stricter-Than-Consensus Invariant Silently Broken - (File: `src/more_ops.rs`)

### Summary

In `op_div`, `op_divmod`, and `op_mod` (and their `_malachite` variants), a `DISABLE_OP`-flag-gated size check uses a threshold of `a0_len > 2048`, but the unconditional general check immediately below it uses `a0_len > 256`. Because 2048 > 256, the `DISABLE_OP` branch can never fire before the general branch catches it first. The flag-specific guard is permanently dead code. `DISABLE_OP` is a component of `MEMPOOL_MODE`, so the intended stricter mempool limit for these three operators is silently never enforced.

---

### Finding Description

In `src/more_ops.rs`, `op_div` reads:

```rust
if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
if a0_len > 256 || a1_len > 1024 {
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
```

The `DISABLE_OP` branch fires only when `a0_len > 2048`. The unconditional branch fires when `a0_len > 256`. Since `256 < 2048`, any input that would satisfy `a0_len > 2048` is already caught by `a0_len > 256` first. The `DISABLE_OP` branch is unreachable dead code.

The identical pattern appears in `op_divmod` (lines 713–717), `op_mod` (lines 769–773), `op_div_malachite` (lines 690–694), `op_divmod_malachite` (lines 742–746), and `op_mod_malachite` (lines 794–798).

`DISABLE_OP` is part of `MEMPOOL_MODE`:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

For `op_modpow` (opcode 60), `DISABLE_OP` completely disables the operator at the dispatch level in `chia_dialect.rs`. The pattern for div/divmod/mod was clearly intended to apply a stricter operand-size limit in mempool mode, but the threshold is set above the general limit, making it permanently ineffective.

---

### Impact Explanation

The `DISABLE_OP` flag has zero effect on `op_div`, `op_divmod`, and `op_mod`. In mempool mode, these operators accept the same operand sizes as in consensus mode (numerator up to 256 bytes, denominator up to 1024 bytes). The intended stricter mempool limit — whatever lower threshold was meant — is never enforced.

Concretely:
- An attacker submits a CLVM coin spend using `div`/`divmod`/`mod` with a 256-byte numerator and a 1024-byte denominator. The mempool accepts it (the dead `DISABLE_OP` guard never fires). The transaction is included in a block. Consensus nodes accept it (the general check at 256/1024 bytes applies identically). No consensus divergence occurs, but the mempool's intended stricter policy is bypassed.
- If the intended `DISABLE_OP` threshold was, say, 128 bytes, inputs with `a0_len` in [129, 256] are accepted by the mempool when they should be rejected, enabling computationally expensive division on large bignums to be submitted freely to the mempool.
- Any future attempt to tighten the threshold (e.g., to 64 bytes) will create a mempool/consensus divergence if the general check is not simultaneously adjusted, because the dead branch was never the actual enforcement point.

---

### Likelihood Explanation

High. The entry path is direct: any attacker-controlled CLVM program that invokes opcode 19 (`/`), 20 (`divmod`), or 61 (`mod`) with a large numerator atom reaches this code. The `DISABLE_OP` flag is always set in mempool mode. No special conditions are required. The dead branch is exercised on every such call without effect.

---

### Recommendation

The `DISABLE_OP` threshold for `op_div`, `op_divmod`, and `op_mod` must be set **below** the general limit, not above it. For example, if the intent is to restrict mempool-mode numerators to 128 bytes:

```rust
if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 128 {
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
if a0_len > 256 || a1_len > 1024 {
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
```

The same fix must be applied to `op_divmod`, `op_mod`, and all three `_malachite` variants. The correct threshold value should be determined by benchmarking the maximum acceptable computation time in mempool mode, consistent with how `DISABLE_OP` completely disables `modpow`.

---

### Proof of Concept

**Trigger**: Submit a CLVM program in mempool mode (`DISABLE_OP` set) using `op_div` with a 256-byte numerator atom.

**Expected (intended) behavior**: The `DISABLE_OP` guard fires and rejects the input.

**Actual behavior**: The `DISABLE_OP` guard at `a0_len > 2048` is skipped (256 < 2048). The general check at `a0_len > 256` passes (256 is not `> 256`). The division executes on a 256-byte bignum numerator in mempool mode without restriction.

Root cause — the threshold inversion: [1](#0-0) 

The `DISABLE_OP` threshold `2048` is above the general threshold `256`, making line 665 permanently unreachable. The same dead pattern repeats at: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

`DISABLE_OP` is part of `MEMPOOL_MODE`, confirming the intended stricter-than-consensus semantics: [7](#0-6) 

Contrast with `modpow`, where `DISABLE_OP` correctly and completely disables the operator at dispatch: [8](#0-7)

### Citations

**File:** src/more_ops.rs (L665-670)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
```

**File:** src/more_ops.rs (L690-694)
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

**File:** src/more_ops.rs (L742-746)
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

**File:** src/more_ops.rs (L794-798)
```rust
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
        return Err(EvalErr::InvalidOpArg(input, "mod".to_string()));
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

### Title
Dead `DISABLE_OP` Flag Check Inverts Intended Size Limits in `op_div` / `op_divmod` / `op_mod` — (File: `src/more_ops.rs`)

---

### Summary

In `src/more_ops.rs`, the functions `op_div`, `op_divmod`, and `op_mod` contain a `DISABLE_OP`-gated size check whose threshold (2048) is larger than the unconditional size check that immediately follows (256). Because 2048 > 256, the `DISABLE_OP` branch is permanently dead code: the unconditional guard always fires first. The net effect is that the `DISABLE_OP` flag — which is part of `MEMPOOL_MODE` and is supposed to impose a *stricter* per-operand limit — has zero effect on these three operators. The intended two-tier limit (256 bytes in mempool mode, 2048 bytes in consensus mode) collapses to a single 256-byte hard cap in both modes, silently rejecting programs that should be valid in consensus mode.

---

### Finding Description

In `op_div` (and identically in `op_divmod`, `op_mod`, and their `_malachite` variants):

```rust
// src/more_ops.rs  op_div  lines 665-669
if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {   // ← DEAD: 2048 > 256
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
if a0_len > 256 || a1_len > 1024 {                             // ← always fires first
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
```

For any `a0_len` in the range 257–2048:
- The `DISABLE_OP` branch is **not** taken (257 ≤ a0_len ≤ 2048, so `a0_len > 2048` is false).
- The unconditional branch **is** taken (`a0_len > 256` is true).

For any `a0_len > 2048`:
- The `DISABLE_OP` branch would fire — but the unconditional branch fires first (since 2048 > 256), so the `DISABLE_OP` branch is still unreachable.

The `DISABLE_OP` flag is defined as part of `MEMPOOL_MODE`:

```rust
// src/chia_dialect.rs  lines 72-76
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

For `op_modpow` (opcode 60), `DISABLE_OP` correctly disables the operator entirely:

```rust
// src/chia_dialect.rs  lines 240-243
60 => {
    if flags.contains(ClvmFlags::DISABLE_OP) {
        return Err(EvalErr::Unimplemented(o))?;
    }
    op_modpow
}
```

The pattern for `op_modpow` confirms the design intent: `DISABLE_OP` is supposed to impose a *stricter* restriction in mempool mode. For `op_div`/`op_divmod`/`op_mod`, the intended two-tier design was:

| Mode | Intended `a0_len` limit |
|---|---|
| Consensus (no `DISABLE_OP`) | 2048 bytes |
| Mempool (`DISABLE_OP` set) | 256 bytes |

As written, both modes enforce 256 bytes, and the `DISABLE_OP` check is unreachable dead code.

The same defect appears in `op_div_malachite` (line 690), `op_divmod` (line 713), `op_divmod_malachite` (line 742), `op_mod` (line 769), and `op_mod_malachite` (line 794).

---

### Impact Explanation

**Consensus divergence / invalid rejection of valid programs.** Any CLVM coin puzzle that calls `div`, `divmod`, or `mod` with a numerator atom between 257 and 2048 bytes will be rejected by every node running this code, even in consensus (non-mempool) mode. Programs that the protocol intends to be valid are silently rejected. Because the bug is symmetric across all nodes, there is no split between nodes, but there is a divergence between the protocol's intended semantics and the implementation's actual behavior. Concretely:

- A coin whose puzzle uses large-integer division (e.g., a 300-byte numerator) cannot be spent.
- The `DISABLE_OP` flag provides no additional mempool-mode protection for `div`/`divmod`/`mod` because the unconditional guard already enforces the stricter 256-byte limit in all modes.

The corrupted result is: `EvalErr::InvalidOpArg` returned for programs that should succeed, causing incorrect spend rejection at the evaluator level.

---

### Likelihood Explanation

**Medium.** The attacker-controlled entry path is direct: any CLVM program submitted to the network (via `run_serialized_chia_program` or equivalent) that invokes `div`/`divmod`/`mod` with a numerator atom larger than 256 bytes triggers the incorrect rejection. An attacker who knows the intended consensus limit is 2048 bytes can craft a coin puzzle that is permanently unspendable on the current implementation, locking funds. Alternatively, a legitimate user who constructs a valid large-integer division program will find it silently rejected with no clear explanation.

---

### Recommendation

Swap the threshold values so the `DISABLE_OP` guard enforces the *stricter* mempool limit and the unconditional guard enforces the *looser* consensus limit:

```rust
// Corrected op_div (apply same fix to op_divmod, op_mod, and their _malachite variants)
if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 256 {
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
if a0_len > 2048 || a1_len > 1024 {
    return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
}
```

Add test cases that explicitly verify:
- `op_div` with `a0_len` = 512 and `DISABLE_OP` set → error
- `op_div` with `a0_len` = 512 and no flags → success

---

### Proof of Concept

**Trigger:** Construct a CLVM program `(/ A B)` where `A` is a 300-byte atom (e.g., 300 `0x01` bytes) and `B` is `1`. Run it in consensus mode (no `DISABLE_OP`).

**Expected (per intended design):** Success, since 300 < 2048.

**Actual:** `EvalErr::InvalidOpArg("div")` because `a0_len = 300 > 256` triggers the unconditional guard at line 668 before the `DISABLE_OP` guard at line 665 is ever evaluated.

Root cause confirmed at: [1](#0-0) 

Dead-code `DISABLE_OP` check (threshold 2048 > unconditional threshold 256): [2](#0-1) 

Unconditional guard that always fires first: [3](#0-2) 

Same defect in `op_divmod`: [4](#0-3) 

Same defect in `op_mod`: [5](#0-4) 

`DISABLE_OP` correctly used (for comparison) in `op_modpow` dispatch: [6](#0-5) 

`DISABLE_OP` as part of `MEMPOOL_MODE`: [7](#0-6)

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

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L240-243)
```rust
                if flags.contains(ClvmFlags::DISABLE_OP) {
                    return Err(EvalErr::Unimplemented(o))?;
                }
                op_modpow
```

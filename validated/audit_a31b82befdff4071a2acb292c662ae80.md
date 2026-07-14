### Title
Dead `DISABLE_OP` Dividend-Size Guard Checks Wrong Threshold, Rendering Mempool Restriction Inert in `op_div` / `op_divmod` / `op_mod` — (File: `src/more_ops.rs`)

---

### Summary

In `op_div`, `op_divmod`, `op_mod`, and their malachite variants, a guard conditioned on `ClvmFlags::DISABLE_OP` checks `a0_len > 2048` for the dividend. Because the unconditional general guard immediately below checks `a0_len > 256`, the `DISABLE_OP` branch is permanently dead code: the general guard always fires first. The `DISABLE_OP` flag therefore has zero effect on dividend-size enforcement for these three operators, directly mirroring the report's pattern of checking the wrong variable in a guard that was supposed to enforce a distinct invariant.

---

### Finding Description

In all six affected functions the code reads:

```rust
// op_div (line 665), op_divmod (line 713), op_mod (line 769)
// and their _malachite twins (lines 690, 742, 794)
if flags.contains(ClvmFlags::DISABLE_OP) && a0
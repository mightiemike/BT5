### Title
Unchecked Post-Multiplication Cost Overflow in `op_unknown` Enables Undercharged Execution — (File: `src/more_ops.rs`)

### Summary

`op_unknown` in `src/more_ops.rs` validates the base cost against `max_cost` **before** multiplying by `(cost_multiplier + 1)`, but only checks the post-multiplication result against `u32::MAX`, not against `max_cost`. In Rust release builds, `u64` overflow wraps silently. An attacker who controls CLVM bytes can craft an unknown operator whose base cost is a precise multiple of `2^32` and whose `cost_multiplier` is `2^32 − 1`, causing the multiplication to wrap to `0`. The operator then returns `Reduction(0, nil)`, consuming zero cost budget regardless of the intended charge.

### Finding Description

In `src/more_ops.rs`, `op_unknown` computes cost in two stages:

1. A base cost is accumulated per argument (cost functions 0–3).
2. The base cost is multiplied by `(cost_multiplier + 1)`.

The guard ordering is:

```rust
// src/more_ops.rs  ~line 258-266
assert!(cost > 0);

check_cost(cost, max_cost)?;          // ← checks PRE-multiplication cost only
cost *= cost_multiplier + 1;          // ← u64 × u64, wraps silently in release
if cost > u32::MAX as u64 {           // ← only rejects > 4 294 967 295, not > max_cost
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`cost_multiplier` is decoded as `u64` from up to 4 operator bytes, so its maximum value is `u32::MAX = 4 294 967 295`, making `cost_multiplier + 1` at most `2^32 = 4 294 967 296`.

**Overflow scenario (cost_function = 1):**

- Base cost formula: `ARITH_BASE_COST(99) + n × ARITH_COST_PER_ARG(320) + total_bytes × ARITH_COST_PER_BYTE(3)`
- Target base cost: exactly `2^32 = 4 294 967 296`
- `check_cost(4 294 967 296, 11e9)` → passes (Chia block limit ≈ 11 billion)
- `cost *= 2^32` → `4 294 967 296 × 2^32 = 2^64 ≡ 0 (mod 2^64)` → wraps to `0`
- `0 > u32::MAX` → false → returns `Reduction(0, nil)`

Since `gcd(320, 3) = 1`, integer solutions for `n × 320 + total_bytes × 3 = 4 294 967 197` always exist. With `n ≈ 13.4 million` zero-byte quoted atoms, argument-evaluation cost is `≈ 268 million`, well within the
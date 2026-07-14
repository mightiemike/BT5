### Title
Pre-Multiplication Cost Guard Bypassed by u64 Overflow in `op_unknown` — (`File: src/more_ops.rs`)

### Summary

`op_unknown` in `src/more_ops.rs` performs a cost guard check (`check_cost`) on the **pre-multiplication** cost value, then multiplies by `cost_multiplier + 1`. If the product overflows `u64`, the wrapped result can be ≤ `u32::MAX`, causing the function to return a near-zero cost for an otherwise expensive unknown opcode — bypassing the cost limit entirely.

### Finding Description

In `op_unknown` (lines 258–266 of `src/more_ops.rs`):

```rust
assert!(cost > 0);

check_cost(cost, max_cost)?;          // ← guards pre-multiplication cost
cost *= cost_multiplier + 1;          // ← multiplier applied AFTER guard
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
``` [1](#0-0) 

The guard at line 260 ensures `cost ≤ max_cost`. Then line 261 multiplies by `(cost_multiplier + 1)`. `cost_multiplier` is decoded as a `u64` from up to 4 bytes of the opcode, so `cost_multiplier + 1` can be as large as `2^32 = 4294967296`:

```rust
let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
    Some(v) => v as u64,
    ...
};
``` [2](#0-1) 

`Cost` is `u64`: [3](#0-2) 

With `cost_multiplier = u32::MAX` and `cost = 2^32` (both reachable under Chia's block limit of ~11 billion), the product is `2^32 × 2^32 = 2^64`, which wraps to **0** in Rust release mode (default wrapping semantics). The subsequent check `if cost > u32::MAX` evaluates to `false`, and the function returns `Ok(Reduction(0, nil))` — a cost of zero for a non-trivial opcode.

The analog to the Peapods bug is exact:
- **Peapods**: `addInterest(false)` conditionally updates state; `toShares()` calculates based on stale state; `repayAsset()` uses updated state → mismatch causes revert.
- **clvm_rs**: `check_cost(cost, max_cost)` guards the pre-multiplication value; `cost *= cost_multiplier + 1` changes the value; the post-multiplication guard uses `u32::MAX` (not `max_cost`) → mismatch allows overflow to produce cost = 0.

### Impact Explanation

An attacker who can submit CLVM programs (e.g., via a Chia spend bundle) can craft an unknown opcode whose bytes set `cost_multiplier = u32::MAX` and whose arguments drive the pre-multiplication cost to exactly `2^32` (or any multiple of `2^32` within `max_cost`). The returned cost of 0 means the program's accumulated cost is not charged for that opcode, bypassing the block cost limit. This enables:

1. **Undercharged execution**: Programs that should be rejected for exceeding the cost limit are accepted.
2. **Consensus divergence**: Debug builds panic on overflow; release builds wrap. Nodes compiled differently will disagree on program validity.

### Likelihood Explanation

The opcode bytes are fully attacker-controlled in any CLVM program. Setting `cost_multiplier = u32::MAX` requires only a 4-byte opcode suffix. Driving the pre-multiplication cost to a multiple of `2^32` requires crafting the argument list length/byte count to hit the target — feasible given that cost_function 1/2/3 accumulate cost linearly or quadratically in argument sizes, and `2^32 ≈ 4.3 billion` is well within Chia's ~11 billion block limit. The attacker needs no special privileges.

### Recommendation

Replace the pre-multiplication `check_cost` with a post-multiplication check, and use saturating or checked arithmetic for the multiplication:

```rust
// Use checked_mul to detect overflow
let final_cost = cost.checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::CostExceeded)?;
check_cost(final_cost, max_cost)?;
if final_cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(final_cost as Cost, allocator.nil()))
}
```

### Proof of Concept

Craft an unknown opcode byte sequence where:
- Bytes `op[0..op.len()-1]` decode via `u32_from_u8` to `0xFFFFFFFF` (cost_multiplier = `u32::MAX`)
- `op[op.len()-1]` has bits `[7:6] = 0b01` (cost_function = 1, ARITH-like)
- Arguments are crafted so `ARITH_BASE_COST + n_args × ARITH_COST_PER_ARG + total_bytes × ARITH_COST_PER_BYTE = 2^32`

Result: `check_cost(2^32, max_cost)` passes (since `2^32 < 11e9`), then `2^32 × (u32::MAX + 1) = 2^64 ≡ 0 (mod 2^64)`, `0 > u32::MAX` is false, function returns `Reduction(0, nil)`. The program's cost budget is not consumed. [1](#0-0) [4](#0-3)

### Citations

**File:** src/more_ops.rs (L202-207)
```rust
    let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
        Some(v) => v as u64,
        None => {
            return Err(EvalErr::Invalid(o))?;
        }
    };
```

**File:** src/more_ops.rs (L258-266)
```rust
    assert!(cost > 0);

    check_cost(cost, max_cost)?;
    cost *= cost_multiplier + 1;
    if cost > u32::MAX as u64 {
        Err(EvalErr::Invalid(o))?
    } else {
        Ok(Reduction(cost as Cost, allocator.nil()))
    }
```

**File:** src/cost.rs (L3-3)
```rust
pub type Cost = u64;
```

**File:** src/cost.rs (L5-10)
```rust
pub fn check_cost(cost: Cost, max_cost: Cost) -> Result<()> {
    if cost > max_cost {
        Err(EvalErr::CostExceeded)
    } else {
        Ok(())
    }
```

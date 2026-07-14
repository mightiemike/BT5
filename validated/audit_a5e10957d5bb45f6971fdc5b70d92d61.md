### Title
Integer Division Truncation in `op_multiply` Quadratic Cost Term Causes Systematic Cost Undercharging — (File: src/more_ops.rs)

---

### Summary

The `op_multiply` function and the `op_unknown` cost-function-2 path both compute the quadratic component of the multiplication cost as `(l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER` where `MUL_SQUARE_COST_PER_BYTE_DIVIDER = 128`. Whenever `l0 * l1 < 128` — which is true for any pair of operands each smaller than 12 bytes — the integer division truncates to exactly 0. The quadratic cost contribution is silently dropped for every such iteration, causing the charged cost to diverge from the intended O(n²) cost formula for the duration that operands remain small.

---

### Finding Description

`MUL_SQUARE_COST_PER_BYTE_DIVIDER` is defined as `128` at line 37:

```rust
const MUL_SQUARE_COST_PER_BYTE_DIVIDER: Cost = 128;
``` [1](#0-0) 

In `op_multiply`, the per-iteration cost accumulation is:

```rust
cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
``` [2](#0-1) 

The same pattern appears in the `no-fastpath` branch and the `U32` fast-path branch: [3](#0-2) [4](#0-3) 

And identically in `op_unknown` cost-function-2:

```rust
cost += (l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
l0 += l1;
``` [5](#0-4) 

The condition `l0 * l1 < 128` is satisfied whenever both operands are ≤ 11 bytes (e.g., `11 × 11 = 121 < 128`). During those iterations the quadratic term evaluates to 0 and the cost accumulator does not increase by the intended amount — exactly mirroring the Roller.sol pattern where `snapAccumulator * dt / snapWindow` stays at 0 until `dt` grows large enough.

In `op_multiply`, `l0` is updated to `limbs_for_int(&total)` after each step, so it tracks the size of the growing product. For a chain of small-integer multiplications the quadratic term remains 0 for the first several iterations and only begins contributing once the accumulated product exceeds 127 bytes — a threshold that may never be reached for programs that multiply many small numbers.

In `op_unknown`, `l0` accumulates as the running sum of argument lengths (`l0 += l1`), so the quadratic term is 0 for the first ~128 single-byte arguments. The base cost computed under this rounding is then multiplied by `cost_multiplier + 1` (up to `u32::MAX`), amplifying the undercharge before the final `u32::MAX` overflow check. [6](#0-5) 

---

### Impact Explanation

The quadratic cost term is the mechanism that makes multiplying large numbers proportionally more expensive than multiplying small ones, matching the real O(n²) CPU cost of big-integer multiplication. When it is silently zeroed, the charged cost diverges from the intended formula. An attacker-controlled CLVM program can supply a sequence of `*` operations whose operands are each ≤ 11 bytes; every such operation pays only the linear term (`(l0 + l1) * 6`) plus the base (`885`), never the quadratic surcharge. The concrete corrupted value is the `Cost` field of the returned `Reduction`: it is lower than the cost model specifies, meaning the program consumes more real CPU time per cost unit than the model assumes. Over a chain of such multiplications the cumulative undercharge is bounded by the number of iterations (each iteration loses at most 127/128 ≈ 1 cost unit), so the absolute gap is small relative to the 11-billion-unit block cost limit. The severity is therefore **low**: the cost model diverges from its own formula, but the divergence is bounded and does not by itself allow a program to exhaust a node's resources in a single block.

---

### Likelihood Explanation

**High.** Any CLVM program that multiplies integers whose byte-lengths satisfy `l0 * l1 < 128` triggers the zero-rounding. This covers all multiplications of numbers up to ~88 bits (11 bytes) × ~88 bits, which is the common case for coin amounts, puzzle hashes truncated to small integers, and typical arithmetic in Chialisp puzzles. The condition is met on every such call without any special crafting.

---

### Recommendation

Replace floor division with ceiling division for the quadratic cost term so that any non-zero product of lengths contributes at least 1 cost unit:

```rust
// before
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;

// after (ceiling division)
cost += (l0 as Cost * l1 + MUL_SQUARE_COST_PER_BYTE_DIVIDER - 1)
            / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

Apply the same fix in `op_unknown` cost-function-2 at line 238. This ensures the cost accumulator always increases by at least 1 when both operands are non-empty, matching the intended O(n²) formula for all input sizes.

---

### Proof of Concept

A CLVM program of the form `(* A B C D … Z)` where every argument is a 1-byte atom (e.g., `0x7f`) exercises the undercharge on every iteration. For each multiplication step:

- `l0` starts at 1 (first arg), `l1 = 1`
- Quadratic term: `(1 × 1) / 128 = 0` — should be `≈ 0.008`, rounds to 0
- After the product grows: `l0 = limbs_for_int(total)`, still 1 for the first several steps

For 127 such arguments the quadratic term is 0 on every iteration. The total charged cost is:

```
MUL_BASE_COST + 126 × (MUL_COST_PER_OP + (l0+1)×MUL_LINEAR_COST_PER_BYTE + 0)
```

whereas the intended cost includes an additional `Σ(l0_i × 1) / 128` across all iterations. The program executes real big-integer multiplications while paying only the linear portion of the cost model, causing the actual CPU cost per charged unit to exceed the model's assumption.

### Citations

**File:** src/more_ops.rs (L37-37)
```rust
const MUL_SQUARE_COST_PER_BYTE_DIVIDER: Cost = 128;
```

**File:** src/more_ops.rs (L238-239)
```rust
                cost += (l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                l0 += l1;
```

**File:** src/more_ops.rs (L260-265)
```rust
    check_cost(cost, max_cost)?;
    cost *= cost_multiplier + 1;
    if cost > u32::MAX as u64 {
        Err(EvalErr::Invalid(o))?
    } else {
        Ok(Reduction(cost as Cost, allocator.nil()))
```

**File:** src/more_ops.rs (L615-616)
```rust
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

**File:** src/more_ops.rs (L623-624)
```rust
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

**File:** src/more_ops.rs (L643-644)
```rust
            cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
            cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

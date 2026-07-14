### Title
`op_unknown` Cost Check Applied to Pre-Multiplied Value, Not Final Scaled Cost — (`File: src/more_ops.rs`)

### Summary
In `op_unknown`, the `check_cost` guard is applied to the raw (pre-multiplied) cost, but the value actually returned to the caller is `cost * (cost_multiplier + 1)`. This is a direct arithmetic semantic mismatch: the enforcement check operates on the wrong scale. In the worst case, when the pre-multiplied cost is a specific multiple of `2^(64-k)` (where `2^k` is the highest power of 2 dividing `cost_multiplier + 1`), the `u64` multiplication wraps to zero, and `op_unknown` returns `Reduction(0, nil)` — a cost of zero — for an operation that should have been rejected or charged a large cost.

### Finding Description

`op_unknown` computes a base cost (the "pre-multiplied cost") from the argument list, then multiplies it by `cost_multiplier + 1` to produce the final cost. The `check_cost` call at line 260 is placed **before** the multiplication:

```rust
// src/more_ops.rs lines 258-266
assert!(cost > 0);

check_cost(cost, max_cost)?;       // ← checks PRE-multiplied cost
cost *= cost_multiplier + 1;       // ← scales AFTER the check
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))  // ← returns POST-multiplied cost
}
``` [1](#0-0) 

`cost_multiplier` is decoded from the opcode bytes as a `u32` (max `u32::MAX`), cast to `u64`: [2](#0-1) 

The `check_cost` function simply compares two `u64` values: [3](#0-2) 

Because `check_cost` sees only the pre-multiplied cost, the post-multiplied cost returned to `run_program` can be arbitrarily larger than `max_cost`. In the normal (non-overflow) case the caller's loop catches this: [4](#0-3) 

But in the **overflow case**, Rust's wrapping `u64` multiplication produces a result that is small (potentially 0), passes the `u32::MAX` guard, and is returned as a legitimate low cost.

**Concrete overflow path:**

- Choose opcode bytes `[0x7F, 0xFF, 0xFF, 0xFF, 0x40]` → `cost_multiplier = 0x7FFFFFFF = 2^31 − 1`, `cost_multiplier + 1 = 2^31`, `cost_function = 1` (add-like).
- Craft arguments so the pre-multiplied cost equals exactly `2^33 = 8 589 934 592` (within Chia's `max_cost ≈ 11 × 10^9`).
- `cost × (cost_multiplier + 1) = 2^33 × 2^31 = 2^64 ≡ 0 (mod 2^64)`.
- `0 ≤ u32::MAX` → `op_unknown` returns `Ok(Reduction(0, nil))`.

The opcode `[0x7F, 0xFF, 0xFF, 0xFF, …]` does not start with `0xFF 0xFF`, so it is not reserved: [5](#0-4) 

`op_unknown` is reachable whenever `NO_UNKNOWN_OPS` is not set (consensus/block-validation mode): [6](#0-5) [7](#0-6) 

`MEMPOOL_MODE` sets `NO_UNKNOWN_OPS`, so the vulnerability is only reachable in consensus (non-mempool) mode: [8](#0-7) 

### Impact Explanation

When `op_unknown` returns cost 0, the running cost accumulator in `run_program` is not incremented for that call (beyond the fixed `OP_COST = 1`). An attacker can embed this opcode in a loop-like CLVM structure and invoke it repeatedly, each time performing O(argument-count) work inside `op_unknown`'s argument-scanning loop — work that is bounded by `max_cost / ARITH_COST_PER_ARG` per call but charged at cost 0. This constitutes **undercharged execution**: the node performs significantly more CPU work than the declared cost budget permits, enabling a resource-exhaustion / DoS attack against block validators running in consensus mode.

**Impact:** High (undercharged execution enabling DoS against consensus-mode validators)

### Likelihood Explanation

- Chia's consensus `max_cost ≈ 11 × 10^9 > 2^33`, so the required pre-multiplied cost value is within range.
- The attacker controls both the opcode bytes (choosing `cost_multiplier`) and the argument list (choosing the pre-multiplied cost). Hitting the exact target value requires solving a linear Diophantine equation over argument counts and sizes — feasible with mixed-size argument lists.
- The attack is only reachable in consensus mode (not mempool mode), limiting the attack surface to block validation.

**Likelihood:** Medium

### Recommendation

Move `check_cost` to after the multiplication, and guard against overflow explicitly:

```rust
// Correct ordering
cost = cost.saturating_mul(cost_multiplier + 1);
if cost > u32::MAX as u64 {
    return Err(EvalErr::Invalid(o))?;
}
check_cost(cost, max_cost)?;
Ok(Reduction(cost as Cost, allocator.nil()))
```

Alternatively, check the pre-multiplied cost against the scaled limit before multiplying:

```rust
check_cost(cost, max_cost / (cost_multiplier + 1))?;
cost *= cost_multiplier + 1;
```

### Proof of Concept

1. Construct a CLVM program in consensus mode (no `NO_UNKNOWN_OPS` flag).
2. Use opcode bytes `[0x7F, 0xFF, 0xFF, 0xFF, 0x40]`:
   - `cost_multiplier = 2^31 − 1`, `cost_function = 1`.
3. Attach an argument list of atoms whose total cost (per the add-like formula at lines 211–221) equals exactly `2^33 = 8 589 934 592`.
4. `check_cost(2^33, max_cost)` passes (since `2^33 < 11 × 10^9`).
5. `cost *= 2^31` → `2^33 × 2^31 = 2^64 ≡ 0 (mod 2^64)`.
6. `0 ≤ u32::MAX` → returns `Reduction(0, nil)`.
7. Wrap this opcode call in a recursive CLVM structure to invoke it many times; each invocation scans the full argument list at cost 0, consuming CPU far beyond the declared budget. [9](#0-8)

### Citations

**File:** src/more_ops.rs (L197-199)
```rust
    if op.is_empty() || (op.len() >= 2 && op[0] == 0xff && op[1] == 0xff) {
        Err(EvalErr::Reserved(o))?;
    }
```

**File:** src/more_ops.rs (L201-207)
```rust
    let cost_function = (op[op.len() - 1] & 0b11000000) >> 6;
    let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
        Some(v) => v as u64,
        None => {
            return Err(EvalErr::Invalid(o))?;
        }
    };
```

**File:** src/more_ops.rs (L209-266)
```rust
    let mut cost = match cost_function {
        0 => 1,
        1 => {
            let mut cost = ARITH_BASE_COST;
            let mut byte_count: u64 = 0;
            while let Some((arg, rest)) = allocator.next(args) {
                args = rest;
                cost += ARITH_COST_PER_ARG;
                let len = atom_len(allocator, arg, "unknown op")?;
                byte_count += len as u64;
                check_cost(cost + (byte_count as Cost * ARITH_COST_PER_BYTE), max_cost)?;
            }
            cost + (byte_count * ARITH_COST_PER_BYTE)
        }
        2 => {
            let mut cost = MUL_BASE_COST;
            let mut first_iter: bool = true;
            let mut l0: u64 = 0;
            while let Some((arg, rest)) = allocator.next(args) {
                args = rest;
                let len = atom_len(allocator, arg, "unknown op")?;
                if first_iter {
                    l0 = len as u64;
                    first_iter = false;
                    continue;
                }
                let l1 = len as u64;
                cost += MUL_COST_PER_OP;
                cost += (l0 + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                l0 += l1;
                check_cost(cost, max_cost)?;
            }
            cost
        }
        3 => {
            let mut cost = CONCAT_BASE_COST;
            while let Some((arg, rest)) = allocator.next(args) {
                args = rest;
                let len = atom_len(allocator, arg, "unknown op")?;
                cost += CONCAT_COST_PER_ARG;
                cost += CONCAT_COST_PER_BYTE * (len as Cost);
                check_cost(cost, max_cost)?;
            }
            cost
        }
        _ => 1,
    };

    assert!(cost > 0);

    check_cost(cost, max_cost)?;
    cost *= cost_multiplier + 1;
    if cost > u32::MAX as u64 {
        Err(EvalErr::Invalid(o))?
    } else {
        Ok(Reduction(cost as Cost, allocator.nil()))
    }
```

**File:** src/cost.rs (L1-10)
```rust
use crate::error::{EvalErr, Result};

pub type Cost = u64;

pub fn check_cost(cost: Cost, max_cost: Cost) -> Result<()> {
    if cost > max_cost {
        Err(EvalErr::CostExceeded)
    } else {
        Ok(())
    }
```

**File:** src/run_program.rs (L514-516)
```rust
            if cost > effective_max_cost {
                return Err(EvalErr::CostExceeded);
            }
```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L78-90)
```rust
fn unknown_operator(
    allocator: &mut Allocator,
    o: NodePtr,
    args: NodePtr,
    flags: ClvmFlags,
    max_cost: Cost,
) -> Response {
    if flags.contains(ClvmFlags::NO_UNKNOWN_OPS) {
        Err(EvalErr::Unimplemented(o))?
    } else {
        op_unknown(allocator, o, args, max_cost)
    }
}
```

**File:** src/chia_dialect.rs (L285-287)
```rust
    fn allow_unknown_ops(&self) -> bool {
        !self.flags.contains(ClvmFlags::NO_UNKNOWN_OPS)
    }
```

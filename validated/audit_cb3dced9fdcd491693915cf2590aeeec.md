### Title
u64 Cost Multiplication Overflow in `op_unknown` Allows Undercharged Execution — (`src/more_ops.rs`)

### Summary

In `op_unknown`, the final cost is computed by multiplying a per-argument cost accumulator (`cost: u64`) by `cost_multiplier + 1` (also `u64`). In Rust release builds, integer overflow wraps silently. When the product exceeds `u64::MAX`, the wrapped result can be arbitrarily small — including zero — and passes the only post-multiplication guard (`cost > u32::MAX`). An attacker who controls the opcode bytes and argument list can craft a CLVM program whose unknown-op cost silently wraps to a value near zero, bypassing the blockchain cost limit.

---

### Finding Description

In `src/more_ops.rs`, `op_unknown` computes cost in two stages:

**Stage 1 — per-argument accumulation (bounded by `max_cost`):**

```rust
let mut cost = match cost_function {
    1 => {
        let mut cost = ARITH_BASE_COST;          // 99
        let mut byte_count: u64 = 0;
        while let Some((arg, rest)) = allocator.next(args) {
            cost += ARITH_COST_PER_ARG;          // 320 per arg
            byte_count += len as u64;
            check_cost(cost + (byte_count as Cost * ARITH_COST_PER_BYTE), max_cost)?;
        }
        cost + (byte_count * ARITH_COST_PER_BYTE)
    }
    // ...
};
```

`check_cost` ensures `cost ≤ max_cost` after the loop. [1](#0-0) 

**Stage 2 — multiplication by `cost_multiplier + 1`, then a single guard:**

```rust
check_cost(cost, max_cost)?;          // cost ≤ max_cost here
cost *= cost_multiplier + 1;          // ← can silently overflow u64
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
``` [2](#0-1) 

`cost_multiplier` is extracted from the opcode bytes as a `u32` (at most `u32::MAX = 4,294,967,295`), then widened to `u64`:

```rust
let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
    Some(v) => v as u64,
    None => { return Err(EvalErr::Invalid(o))?; }
};
``` [3](#0-2) 

So `cost_multiplier + 1` can be at most `2^32 = 4,294,967,296`. With Chia's 11-billion cost limit, `max_cost` can be up to `11 × 10^9`. The maximum product is:

```
11 × 10^9  ×  4,294,967,296  ≈  47.2 × 10^18  >  u64::MAX (≈ 18.4 × 10^18)
```

In Rust release mode, this wraps silently. The attacker can choose `cost` and `cost_multiplier` so that `(cost × (cost_multiplier + 1)) mod 2^64 ≤ u32::MAX`, passing the only post-multiplication guard and returning a near-zero cost.

The `Cost` type is `u64` throughout: [4](#0-3) 

---

### Impact Explanation

- An attacker crafts a CLVM program in lenient (consensus) mode containing an unknown opcode with `cost_function ∈ {1,2,3}`, a large argument list (to drive `cost` above `u64::MAX / (u32::MAX + 1) ≈ 4.3 × 10^9`), and `cost_multiplier = u32::MAX`.
- The multiplication wraps to a small value (e.g., 0), which is returned as the op's cost.
- The program's accumulated cost barely increases, allowing it to continue executing past the blockchain cost limit.
- This is **undercharged execution**: a computationally expensive program is accepted as cheap, enabling DoS of Chia full nodes and potential consensus divergence between debug builds (which panic on overflow) and release builds (which wrap).

---

### Likelihood Explanation

- The attacker fully controls the opcode bytes (setting `cost_multiplier`) and the argument list (setting `cost`).
- Lenient mode (`allow_unknown_ops = true`) is the default in Chia consensus, making unknown ops reachable on every full node.
- With `MAX_NUM_PAIRS = 62,500,000`, providing ~13–34 million arguments to push `cost` above 4.3 billion is within allocator limits.
- The attacker has two independent degrees of freedom (`cost` via arguments, `cost_multiplier` via opcode bytes) to engineer the exact overflow target. [5](#0-4) 

---

### Recommendation

Replace the unchecked multiplication with a checked or saturating variant, and reject the op if the product overflows or exceeds the allowed maximum:

```rust
let true_cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;
if true_cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(true_cost as Cost, allocator.nil()))
}
```

This mirrors the fix pattern from the external report: compute in the wider type first, then validate before narrowing.

---

### Proof of Concept

Construct a CLVM program in lenient mode with an unknown opcode where:

- Last byte of opcode = `0b01_000000` → `cost_function = 1`, zero low bits
- First 4 bytes of opcode = `0xFF, 0xFF, 0xFF, 0xFF` → `cost_multiplier = u32::MAX = 4,294,967,295`, so `cost_multiplier + 1 = 2^32`
- Argument list: ~13,421,772 small atoms → `cost ≈ 99 + 13,421,772 × 320 ≈ 4,294,967,139`

Choose argument count so that `cost × 2^32 mod 2^64 < u32::MAX`. Because `cost ≈ 2^32`, the product `≈ 2^64 ≡ 0 mod 2^64`. The function returns `Ok(Reduction(0, nil))` — a zero-cost operation — while the true cost should have been ~18.4 × 10^18, far exceeding any cost limit. [2](#0-1)

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

**File:** src/more_ops.rs (L209-256)
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

**File:** src/allocator.rs (L17-18)
```rust
const MAX_NUM_ATOMS: usize = 62500000;
const MAX_NUM_PAIRS: usize = 62500000;
```

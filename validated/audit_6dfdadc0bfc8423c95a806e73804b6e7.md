### Title
u64 Overflow in `op_unknown` Cost Multiplier Produces Zero-Cost Execution — (`File: src/more_ops.rs`)

### Summary

In `op_unknown` (`src/more_ops.rs`), the final cost is computed as `cost *= cost_multiplier + 1` using unchecked u64 arithmetic. In Rust release builds, this multiplication wraps on overflow. The subsequent guard `if cost > u32::MAX as u64` only rejects the *wrapped* result, not the pre-wrap value. An attacker can craft an unknown opcode whose argument list drives `cost` to a specific multiple of 2^33, pair it with a `cost_multiplier` of 2^31 − 1, and cause the product to wrap to exactly 0 — returning `Ok(Reduction(0, nil))` and charging zero cost for the opcode.

---

### Finding Description

`op_unknown` computes a base cost from the opcode's cost-function bits (0–3) and then scales it:

```rust
// src/more_ops.rs lines 258-266
assert!(cost > 0);
check_cost(cost, max_cost)?;          // ensures cost ≤ max_cost before multiply
cost *= cost_multiplier + 1;          // ← unchecked u64 multiply; wraps in release
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`cost_multiplier` is decoded from the opcode prefix bytes via `u32_from_u8`, so it is at most `u32::MAX` = 4 294 967 295, giving `cost_multiplier + 1` up to 2^32. [1](#0-0) 

The reserved-opcode guard only blocks prefixes that begin with `0xff 0xff`:

```rust
// src/more_ops.rs lines 197-199
if op.is_empty() || (op.len() >= 2 && op[0] == 0xff && op[1] == 0xff) {
    Err(EvalErr::Reserved(o))?;
}
``` [2](#0-1) 

A 5-byte opcode whose first byte is `0x7f` is not reserved. Its 4-byte prefix `[0x7f, 0xff, 0xff, 0xff]` decodes to `cost_multiplier = 0x7fff_ffff = 2 147 483 647`, so `cost_multiplier + 1 = 2^31`. [3](#0-2) 

**Concrete trigger (cost-function 1, ARITH-like):**

```
cost = ARITH_BASE_COST + n × ARITH_COST_PER_ARG + total_bytes × ARITH_COST_PER_BYTE
     = 99 + 26 843 545 × 320 + 31 × 3
     = 8 589 934 592   (= 2^33)
``` [4](#0-3) 

Then:

```
cost *= cost_multiplier + 1
     = 2^33 × 2^31 = 2^64  ≡  0  (mod 2^64)   ← wraps in release mode
```

`0 ≤ u32::MAX`, so the guard passes and `Ok(Reduction(0, nil))` is returned — the opcode is executed at **zero cost**.

The same wrap-to-zero is achievable with `cost = 2 × 2^32 = 8 589 934 592` and any `cost_multiplier + 1` that is a power of two whose exponent sums to ≥ 64 with the exponent of `cost`, as long as the opcode prefix does not begin with `0xff 0xff`.

---

### Impact Explanation

`Cost = u64` is the sole resource-accounting primitive for the CLVM execution engine. [5](#0-4) 

A zero-cost unknown opcode lets an attacker include arbitrarily many such opcodes in a single block without consuming any of the block's cost budget. This breaks the block-cost limit that protects full nodes from unbounded computation, and it is a consensus-critical arithmetic error: the reported cost diverges from the true cost of execution.

---

### Likelihood Explanation

The attack requires only crafting a 5-byte opcode atom and a list of ~26 million empty atoms as arguments. Both are valid CLVM atoms; no privileged access, no social engineering, and no dependency on external state are needed. The opcode bytes and argument count are fully attacker-controlled inputs to `run_program`. The arithmetic is deterministic and reproducible across all nodes running the same release binary.

---

### Recommendation

Replace the unchecked multiplication with a checked variant and treat overflow as an error:

```rust
// src/more_ops.rs — replace lines 261-266
cost = cost
    .checked_mul(cost_multiplier + 1)
    .filter(|&c| c <= u32::MAX as u64)
    .ok_or(EvalErr::Invalid(o))?;
Ok(Reduction(cost as Cost, allocator.nil()))
```

This mirrors the pattern used in the farming-contract fix (capping the permissible value at a safe bound before downstream arithmetic can produce incorrect results).

---

### Proof of Concept

```
opcode bytes : [0x7f,

### Citations

**File:** src/more_ops.rs (L23-26)
```rust
const ARITH_BASE_COST: Cost = 99;
const ARITH_COST_PER_ARG: Cost = 320;
const ARITH_COST_PER_BYTE: Cost = 3;

```

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

**File:** src/cost.rs (L1-11)
```rust
use crate::error::{EvalErr, Result};

pub type Cost = u64;

pub fn check_cost(cost: Cost, max_cost: Cost) -> Result<()> {
    if cost > max_cost {
        Err(EvalErr::CostExceeded)
    } else {
        Ok(())
    }
}
```

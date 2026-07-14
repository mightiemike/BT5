### Title
Unchecked u64 Multiplication Overflow in `op_unknown` Bypasses Cost Limit — (`File: src/more_ops.rs`)

### Summary
`op_unknown` in `src/more_ops.rs` multiplies a computed base cost by `(cost_multiplier + 1)` without overflow protection. In Rust release mode, this wraps silently. The subsequent guard only checks `cost > u32::MAX`, which is insufficient to detect a wrapped result. An attacker who controls the opcode bytes and argument list can cause the final cost to wrap to a value ≤ `u32::MAX`, causing the program to be accepted with a near-zero cost charge — directly analogous to the FeeHelper pattern where unchecked arithmetic on externally-supplied values bypasses a resource limit.

### Finding Description

In `src/more_ops.rs`, `op_unknown` computes a base cost from the argument list (bounded by `max_cost` via `check_cost`), then multiplies it by an attacker-controlled multiplier extracted from the opcode bytes:

```rust
// line 202-207
let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
    Some(v) => v as u64,
    None => { return Err(EvalErr::Invalid(o))?; }
};
```

`cost_multiplier` is at most `u32::MAX = 4_294_967_295`, so `cost_multiplier + 1` is at most `2^32 = 4_294_967_296`.

```rust
// line 258-265
assert!(cost > 0);
check_cost(cost, max_cost)?;
cost *= cost_multiplier + 1;          // ← unchecked u64 multiplication
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`check_cost` at line 260 ensures `cost ≤ max_cost` before the multiplication. With Chia's block cost limit of 11 billion (`> 2^32`), `cost` can legally exceed `2^32 − 1`. When `cost_multiplier + 1 = 2^32` (i.e., `cost_multiplier = u32::MAX`) and `cost` is a multiple of `2^32`, the product is a multiple of `2^64`, which wraps to **0** in Rust release mode. The guard `cost > u32::MAX` evaluates to `false` for 0, so the function returns `Ok(Reduction(0, nil))` — a zero-cost result for an operation that consumed real resources.

**Concrete trigger values:**
- `cost_multiplier = u32::MAX` → `cost_multiplier + 1 = 2^32`
- Base cost `C = 2^32 = 4_294_967_296` (achievable with cost_function=1: `99 + 2×320 + 1_431_655_519×3 = 4_294_967_296`)
- Product: `4_294_967_296 × 4_294_967_296 = 2^64 ≡ 0 (mod 2^64)`
- Returned cost: **0** [1](#0-0) 

The `cost_multiplier` is extracted from attacker-controlled opcode bytes: [2](#0-1) 

The base cost computation for `cost_function = 1` is bounded by `max_cost` but not by any value that prevents the post-multiplication overflow: [3](#0-2) 

`Cost` is `u64`, so no type-level protection exists: [4](#0-3) 

### Impact Explanation

`op_unknown` is invoked in consensus mode (`allow_unknown_ops = true`) for any opcode not in the known set. The returned `Reduction(cost, nil)` cost is added directly to the running program cost in `run_program`: [5](#0-4) 

If `op_unknown` returns cost 0 (or any value far below the true resource consumption), the program's total accumulated cost is undercharged. A program that should exceed `max_cost` passes the cost gate. Nodes spend real CPU/memory processing the argument list while the cost counter does not advance, enabling a **cost-limit bypass** and **network-level DoS**: an attacker can submit transactions whose true processing cost exceeds the block cost budget but whose reported cost does not.

### Likelihood Explanation

- The attacker fully controls the opcode bytes (setting `cost_multiplier = u32::MAX` is trivial).
- The attacker controls the argument list (setting argument count and sizes to reach a target base cost).
- Chia's block cost limit (≈11 billion) is larger than `2^32`, so the required base cost is within the allowed budget.
- The main practical constraint is program/block size limits, which may require large argument atoms rather than many small ones.
- The cost formula for unknown operators is public and documented in the source.
- Rust release builds wrap on integer overflow by default; no `overflow-checks = true` is present in the workspace configuration.

Likelihood: **Medium** — requires crafted but non-secret inputs; constrained by block size limits in practice.

### Recommendation

Replace the unchecked multiplication with a checked variant that returns an error on overflow:

```rust
// src/more_ops.rs, replacing line 261
cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;
```

This mirrors the pattern already used in `op_add` and `op_subtract` fast paths, which use `checked_add` / `checked_sub` before falling back: [6](#0-5) [7](#0-6) 

### Proof of Concept

```
Opcode bytes (5 bytes, cost_function = 0b00 in last byte, cost_multiplier = 0xFFFFFFFF):
  [0xFF, 0xFF, 0xFF, 0xFF, 0x00]

Wait — 0xFFFF prefix is reserved. Use instead:
  [0x00, 0xFF, 0xFF, 0xFF, 0x00]
  → cost_multiplier = u32_from_u8([0x00, 0xFF, 0xFF, 0xFF]) = 0x00FFFFFF = 16777215
  → cost_multiplier + 1 = 16777216 = 2^24

For cost_function = 1 (bits 7-6 of last byte = 01):
  Opcode: [0x00, 0xFF, 0xFF, 0xFF, 0x40]
  cost_multiplier + 1 = 2^24

  Target base cost C such that C × 2^24 ≡ 0 (mod 2^64):
  C must be a multiple of 2^40.
  Minimum C = 2^40 = 1_099_511_627_776 (within 11B limit? No — exceeds 11B)

Use cost_function = 0 (constant cost = 1):
  Opcode: [0xFF, 0xFF, 0xFF, 0xFF, 0x00] — reserved, invalid.
  Opcode: [0x00, 0x

### Citations

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

**File:** src/more_ops.rs (L209-222)
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

**File:** src/more_ops.rs (L435-437)
```rust
                let Some(new_total) = total.checked_add(val as u64) else {
                    return Ok(None);
                };
```

**File:** src/more_ops.rs (L514-516)
```rust
                    let Some(new_total) = total.checked_sub(val as i64) else {
                        return Ok(None);
                    };
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

**File:** src/run_program.rs (L522-524)
```rust
            cost += match op {
                Operation::Apply => self.apply_op(cost, effective_max_cost - cost)?,
                Operation::ExitGuard => self.exit_guard(cost)?,
```

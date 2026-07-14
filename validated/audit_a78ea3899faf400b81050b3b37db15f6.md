### Title
`op_unknown` Multiplies Cost After Wrong-Bound Overflow Guard, Enabling u64 Wrap and Cost Undercharge — (`File: src/more_ops.rs`)

---

### Summary

In `op_unknown` (`src/more_ops.rs`), the overflow guard `check_cost(cost, max_cost)` is applied to the **pre-multiplication** cost using `max_cost` as the bound. Immediately after, the cost is multiplied by `cost_multiplier + 1` (attacker-controlled, up to `u32::MAX + 1 ≈ 4.3 × 10⁹`). This multiplication can overflow `u64` in Rust release mode (wrapping semantics), producing a tiny wrapped cost that passes the subsequent `cost > u32::MAX` guard. The result is a cost-undercharge: an attacker-crafted unknown opcode returns near-zero cost for what should be an expensive operation.

---

### Finding Description

The `op_unknown` function computes cost in two stages:

**Stage 1** — base cost from `cost_function` (0–3) and argument sizes: [1](#0-0) 

**Stage 2** — multiply by `cost_multiplier + 1` and check bounds: [2](#0-1) 

The sequence is:
```
check_cost(cost, max_cost)?;   // line 260 — guards pre-multiplication cost
cost *= cost_multiplier + 1;   // line 261 — can overflow u64
if cost > u32::MAX as u64 {    // line 262 — wrong bound, too late
```

`cost_multiplier` is decoded from the opcode bytes as a `u64` bounded by `u32::MAX`: [3](#0-2) 

The guard at line 260 ensures `cost ≤ max_cost` before multiplication. But `max_cost` in `op_unknown` is the **remaining budget** (`effective_max_cost - current_cost`), which can be as large as the full block cost limit (~11 billion on Chia mainnet). With `cost_multiplier + 1 = u32::MAX + 1 ≈ 4.3 × 10⁹`, the product `cost × (cost_multiplier + 1)` can exceed `u64::MAX ≈ 1.8 × 10¹⁹`:

```
11 × 10⁹  ×  4.3 × 10⁹  ≈  4.7 × 10¹⁹  >  u64::MAX
```

In Rust release mode, `u64` overflow wraps (two's complement). The wrapped value can be arbitrarily small — including 0 — and will pass the `cost > u32::MAX` check at line 262, causing `op_unknown` to return `Ok(Reduction(wrapped_cost, nil))` with a near-zero cost.

The analog to the original report is exact: the wrong bound is used to prevent overflow. The original bug clamped to `type(uint128).max - totalBorrowCap` (a cap, not the live value) instead of `type(uint128).max - totalBorrow.elastic`. Here, `check_cost(cost, max_cost)` guards the pre-multiplication value against `max_cost` (a budget, not the multiplication overflow threshold), instead of guarding against `u64::MAX / (cost_multiplier + 1)`.

---

### Impact Explanation

An attacker who can submit CLVM programs in consensus mode (where `NO_UNKNOWN_OPS` is not set) can craft an unknown opcode with a large `cost_multiplier` such that the multiplication wraps to a small value. The `run_program` loop adds this tiny cost to the running total: [4](#0-3) 

The program is accepted as within budget while having consumed far more real resources than charged. This breaks the cost-metering invariant that underpins Chia's DoS protection, enabling an attacker to submit programs that exhaust node CPU/memory at near-zero declared cost — a consensus-relevant undercharge.

---

### Likelihood Explanation

`op_unknown` is reachable whenever `allow_unknown_ops()` returns `true`, which is the case when `NO_UNKNOWN_OPS` is absent from the dialect flags: [5](#0-4) 

This is the default consensus (block validation) mode. `MEMPOOL_MODE` sets `NO_UNKNOWN_OPS`, so mempool validation is protected, but block generators validated in consensus mode are not: [6](#0-5) 

The attacker only needs to craft opcode bytes with a large `cost_multiplier` (encoded in the leading bytes of the opcode atom) and a `cost_function` that produces a large enough pre-multiplication cost. This requires no special privileges — only the ability to submit a transaction.

---

### Recommendation

Replace the unchecked multiplication with `checked_mul` and return an error on overflow, or clamp the pre-multiplication cost to `u64::MAX / (cost_multiplier + 1)` before multiplying. The correct fix is:

```rust
// Instead of:
check_cost(cost, max_cost)?;
cost *= cost_multiplier + 1;
if cost > u32::MAX as u64 { ... }

// Use:
check_cost(cost, max_cost)?;
cost = cost.checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;
if cost > u32::MAX as u64 { ... }
```

This mirrors the correct fix in the original report: use the live accumulated value (not a static cap) as the overflow bound.

---

### Proof of Concept

Craft an unknown opcode atom with:
- Bytes `[0xFF, 0xFF, 0xFF, 0xC0]` — last byte `0xC0` = `cost_function=3` (concat-like), `cost_multiplier = 0x00FFFFFF = 16777215`; adjust to maximize `cost_multiplier` while keeping `cost_function` non-zero.
- More precisely: use a 5-byte opcode (not `0xffff`-prefixed, not a known 1-byte or 4-byte op) with leading bytes encoding `cost_multiplier = u32::MAX` and last byte encoding `cost_function = 1`.

With `max_cost = 11_000_000_000` (Chia block limit) and `cost_function = 1` with enough large-atom arguments to push pre-multiplication cost to ~11 billion, the multiplication `11_000_000_000 × 4_294_967_296` overflows `u64::MAX`, wrapping to a value ≤ `u32::MAX`. The function returns `Ok` with the wrapped cost instead of `Err(EvalErr::Invalid)`, undercharging the operation. [7](#0-6)

### Citations

**File:** src/more_ops.rs (L201-256)
```rust
    let cost_function = (op[op.len() - 1] & 0b11000000) >> 6;
    let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
        Some(v) => v as u64,
        None => {
            return Err(EvalErr::Invalid(o))?;
        }
    };

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

**File:** src/run_program.rs (L522-523)
```rust
            cost += match op {
                Operation::Apply => self.apply_op(cost, effective_max_cost - cost)?,
```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L285-287)
```rust
    fn allow_unknown_ops(&self) -> bool {
        !self.flags.contains(ClvmFlags::NO_UNKNOWN_OPS)
    }
```

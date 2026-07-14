### Title
Unchecked `u64` Multiplication Overflow in `op_unknown` Cost Calculation Allows Cost Undercharging — (File: `src/more_ops.rs`)

---

### Summary

In `op_unknown`, the final cost is computed by multiplying a base cost (already validated against `max_cost`) by `cost_multiplier + 1` using an unchecked `u64 *= u64` operation. In Rust release builds, this silently wraps on overflow. The post-multiplication guard only rejects values `> u32::MAX`, so a wrapped-around value that lands at or below `u32::MAX` passes undetected and is returned as the operation's cost. An attacker who controls the opcode bytes and arguments can engineer the overflow to produce a near-zero reported cost for an operation whose base cost consumed a large fraction of the block cost budget.

---

### Finding Description

In `src/more_ops.rs`, `op_unknown` computes cost in three steps: [1](#0-0) 

```
258: assert!(cost > 0);
260: check_cost(cost, max_cost)?;      // ← cost ≤ max_cost guaranteed here
261: cost *= cost_multiplier + 1;      // ← unchecked u64 multiplication, can wrap
262: if cost > u32::MAX as u64 {
263:     Err(EvalErr::Invalid(o))?
265:     Ok(Reduction(cost as Cost, allocator.nil()))
```

`cost_multiplier` is decoded from the opcode atom bytes as a `u32` (at most `u32::MAX = 4 294 967 295`), then widened to `u64`: [2](#0-1) 

After `check_cost` passes, `cost` is at most `max_cost`. Chia's production block cost limit is ~11 billion (`11 × 10⁹`). With `cost_multiplier = u32::MAX`, the factor is `2³²`. The product `11 × 10⁹ × 2³² ≈ 4.7 × 10¹⁹` exceeds `u64::MAX ≈ 1.84 × 10¹⁹`, so the multiplication wraps. The wrapped result may be ≤ `u32::MAX`, passing the post-multiplication guard and returning a tiny (or zero) cost.

**Concrete example:**

| Variable | Value |
|---|---|
| `cost` (base, pre-multiply) | `2³² = 4 294 967 296` |
| `cost_multiplier` | `u32::MAX = 4 294 967 295` |
| `cost_multiplier + 1` | `2³²` |
| `cost × (cost_multiplier + 1)` | `2⁶⁴ ≡ 0 (mod 2⁶⁴)` |
| Returned cost | **0** |

A base cost of exactly `2³²` is achievable with `cost_function = 1` (add-like): `cost = 99 + 320 × n + 3 × b`. Since `gcd(320, 3) = 1`, all integers ≥ 737 are representable; `2³² ≈ 4.3 × 10⁹` is well within Chia's 11-billion limit and is representable. [3](#0-2) 

---

### Impact Explanation

The returned `Reduction(0, nil)` (or any near-zero cost) is added to the running cost accumulator in `run_program`: [4](#0-3) 

A program can include one or more unknown opcodes whose base cost consumes a large fraction of the block budget but whose reported cost is 0. This allows a transaction to perform far more computational work than the cost limit permits, enabling a denial-of-service attack: validators must execute the expensive argument-processing loop (e.g., ~13 million argument nodes for a base cost of `2³²`) while the cost counter barely advances. Every full node running this code is equally affected, so there is no consensus divergence — the undercharge is universal and the block is accepted by all nodes.

---

### Likelihood Explanation

- The attacker controls both the opcode bytes (setting `cost_multiplier` to any `u32` value) and the argument list (setting the base cost to any value up to `max_cost`).
- The overflow condition requires `cost × (cost_multiplier + 1) ≥ 2⁶⁴` and the wrapped result ≤ `u32::MAX`. Both constraints are satisfiable within Chia's production cost limit.
- Unknown opcodes are a defined, reachable code path in the production interpreter.
- No special privileges, social engineering, or compromised nodes are required — only crafted CLVM bytes submitted as a standard transaction.

---

### Recommendation

Replace the unchecked multiplication with a checked variant and treat overflow as an invalid opcode:

```rust
// Before (line 261):
cost *= cost_multiplier + 1;

// After:
cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;
```

This mirrors the fix applied to the analogous bump-allocator overflow (using `checked_add` instead of `+=`).

---

### Proof of Concept

```clvm
; Opcode bytes: [0xFF, 0xFF, 0xFF, 0xFF, 0x40]
;   - bytes 0..3 = 0xFFFFFFFF → cost_multiplier = u32::MAX
;   - byte 4     = 0x40       → cost_function = 1 (add-like), low 6 bits ignored
;
; Arguments: ~13 421 772 atoms of 0 bytes each
;   → base cost = 99 + 320 × 13 421 772 = 4 294 967 139  (≈ 2^32, within 11B limit)
;   → cost × (u32::MAX + 1) = base_cost × 2^32 wraps to a value ≤ u32::MAX
;   → returned cost ≈ 0
;
; Net effect: the block cost counter advances by ~0 while the validator
; processes ~13 million argument nodes.
(0xFFFFFFFF40 arg arg arg ... )   ; ~13M args
```

The attacker embeds this expression in a CLVM puzzle. The validator executes `op_unknown`, processes all arguments (real CPU work), but the cost accumulator receives 0, leaving room for additional expensive operations in the same block.

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

**File:** src/run_program.rs (L522-523)
```rust
            cost += match op {
                Operation::Apply => self.apply_op(cost, effective_max_cost - cost)?,
```

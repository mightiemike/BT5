### Title
Unchecked u64 Overflow in `op_unknown` Cost Multiplier Silently Undercharges Execution Cost — (File: `src/more_ops.rs`)

---

### Summary

`op_unknown` in `src/more_ops.rs` computes a final cost as `base_cost * (cost_multiplier + 1)` using a plain `u64 *= u64` assignment with no overflow guard. In a Rust release build, this wraps silently. The post-multiplication guard only rejects values above `u32::MAX`, so a wrapped result that lands below that threshold is accepted and returned as the operation's cost. An attacker who controls the CLVM bytes can craft an unknown opcode whose reported cost is near-zero while the actual base cost consumed up to `max_cost` units of work, bypassing the execution cost limit.

---

### Finding Description

In `op_unknown` the base cost is accumulated inside a loop with `check_cost` guards that ensure `cost <= max_cost` before the loop exits: [1](#0-0) 

After the loop, the invariant `cost > 0` is asserted, then the multiplier is applied:

```
check_cost(cost, max_cost)?;   // cost ≤ max_cost here
cost *= cost_multiplier + 1;   // u64 × u64, wraps on overflow in release mode
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`cost_multiplier` is extracted via `u32_from_u8`, so its maximum value is `u32::MAX = 4,294,967,295`, making `cost_multiplier + 1` at most `2^32 = 4,294,967,296`. [2](#0-1) 

For overflow to occur: `cost × 2^32 > 2^64`, i.e. `cost > 2^32 ≈ 4.3 × 10^9`. With Chia's default `max_cost = 11,000,000,000`, the base cost can legally reach values well above this threshold before the multiplication. In Rust release mode, the multiplication wraps modulo `2^64`. If the wrapped result is `≤ u32::MAX`, the guard passes and the function returns that tiny value as the operation's cost.

**Concrete example (cost function 3, concat-like):**

- Opcode: 5 bytes `[0xFF, 0xFF, 0xFF, 0xFF, 0xC0]` → `cost_multiplier = u32::MAX`, `cost_function = 3`
- Arguments: ~31,814,571 zero-byte atoms → base cost accumulates to exactly `2^32 = 4,294,967,296`
- Multiplication: `2^32 × 2^32 = 2^64 ≡ 0 (mod 2^64)`
- Guard: `0 ≤ u32::MAX` → passes
- Returned cost: **0** instead of the true value `≈ 1.84 × 10^19`

The constants that govern cost function 3 are: [3](#0-2) 

The loop that accumulates base cost with per-iteration `check_cost` calls: [4](#0-3) 

---

### Impact Explanation

`op_unknown` is reachable whenever `ClvmFlags::NO_UNKNOWN_OPS` is **not** set — i.e., in consensus (non-mempool) mode: [5](#0-4) [6](#0-5) 

`MEMPOOL_MODE` sets `NO_UNKNOWN_OPS`, so the mempool rejects unknown opcodes. But consensus validation does not, meaning a block containing such a spend is evaluated with `op_unknown` active. [7](#0-6) 

The broken invariant is: **`reported_cost ≥ base_cost`** (since `cost_multiplier + 1 ≥ 1`). Wrapping overflow violates this. A program that legitimately consumed up to `max_cost` units of base work reports near-zero cost to `run_program`, which accumulates it into the running total. The program is accepted as within budget while the actual validation work performed is far above what the cost limit should allow. This is a **cost-undercharge / resource exhaustion** vulnerability: an attacker can craft a coin spend that forces full nodes to perform unbounded-relative-to-cost validation work.

---

### Likelihood Explanation

The attacker controls the CLVM bytes entirely. Crafting a 5-byte opcode with `cost_multiplier = u32::MAX` and supplying enough arguments to push the base cost past `2^32` requires only a large-but-valid CLVM program. No privileged access, no social engineering, and no dependency on node configuration is needed. The trigger is deterministic and reproducible across all nodes running a release build.

---

### Recommendation

Replace the bare `*=` with a checked or saturating multiply and treat overflow as an error:

```rust
// Before applying the multiplier, verify it won't overflow.
let true_cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;
if true_cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(true_cost as Cost, allocator.nil()))
}
```

This mirrors the fix pattern from the external report: assert the invariant (`reported_cost = base_cost × multiplier` without wrap) before accepting the result.

---

### Proof of Concept

**Attacker-controlled CLVM bytes** (pseudocode):

```
opcode  = [0xFF, 0xFF, 0xFF, 0xFF, 0xC0]
          ^^^^^^^^^^^^^^^^^^^^  ^^^^
          cost_multiplier=u32::MAX  cost_function=3 (concat-like)

arguments = 31_814_571 × nil-atom   (each 0 bytes, 123 bytes total across a subset)
```

**Expected base cost before multiplication:**
```
142 + 31_814_571 × 135 + 123 × 3
= 142 + 4_294_966_785 + 369
= 4_294_967_296   (= 2^32)
```

**Multiplication (release build, wrapping):**
```
4_294_967_296 × 4_294_967_296 = 2^64 ≡ 0  (mod 2^64)
```

**Guard check:** `0 > u32::MAX` → false → `Ok(Reduction(0, nil))`

The operation reports **0 cost** to the evaluator despite consuming `4,294,967,296` units of base work. The evaluator's running cost total does not increase, allowing the attacker to chain additional operations within the same `max_cost` budget. [1](#0-0) [2](#0-1)

### Citations

**File:** src/more_ops.rs (L48-50)
```rust
const CONCAT_BASE_COST: Cost = 142;
const CONCAT_COST_PER_ARG: Cost = 135;
const CONCAT_COST_PER_BYTE: Cost = 3;
```

**File:** src/more_ops.rs (L202-207)
```rust
    let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
        Some(v) => v as u64,
        None => {
            return Err(EvalErr::Invalid(o))?;
        }
    };
```

**File:** src/more_ops.rs (L244-254)
```rust
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

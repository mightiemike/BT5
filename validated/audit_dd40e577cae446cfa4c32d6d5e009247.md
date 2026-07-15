### Title
Attacker-Controlled `cost_multiplier` Integer Overflow Enables Zero-Cost Unknown Op Execution — (File: `src/more_ops.rs`)

---

### Summary

In `op_unknown` (`src/more_ops.rs`), the final cost is computed by multiplying a base cost (bounded by `max_cost`) by an attacker-controlled `cost_multiplier + 1` derived from the opcode bytes. This multiplication is performed in unchecked `u64` arithmetic. With a crafted opcode and arguments, the product wraps to zero (or a small value), causing the VM to accept the operation at near-zero cost. This is the direct analog of the reported "no upper bound on fee rate" class: an attacker-controlled parameter (the cost multiplier embedded in opcode bytes) has no overflow guard, allowing the effective cost to be silently reduced to zero.

---

### Finding Description

In `src/more_ops.rs`, `op_unknown` computes the cost of unknown opcodes as follows: [1](#0-0) 

The `cost_multiplier` is extracted from the opcode bytes (up to 4 bytes → at most `u32::MAX = 4294967295`) and widened to `u64`. The base `cost` is then computed from the arguments (bounded by `max_cost` via `check_cost` calls inside the loop): [2](#0-1) 

The sequence is:
1. `check_cost(cost, max_cost)?` — verifies `cost ≤ max_cost` (passes)
2. `cost *= cost_multiplier + 1` — **unchecked u64 multiplication, wraps on overflow**
3. `if cost > u32::MAX as u64` — checks the *wrapped* result, not the true product

With Chia's block cost limit of ~11 billion (`11e9`), the base `cost` can reach up to `11e9` before the multiplication. With `cost_multiplier = 0x7fffffff` (opcode `[0x7f, 0xff, 0xff, 0xff, X]`, not reserved), `cost_multiplier + 1 = 2^31`. Choosing arguments so that the base cost equals exactly `2^33 = 8589934592` (which is `< 11e9`, so `check_cost` passes):

```
2^33 × 2^31 = 2^64 ≡ 0  (mod 2^64)
```

The wrapped result is `0`, which satisfies `0 ≤ u32::MAX`, so the function returns `Reduction(0, allocator.nil())` — **zero cost** for an operation that should cost `2^64` units.

The base cost of `2^33` is achievable for `cost_function = 1` (arith-like): [3](#0-2) 

With `ARITH_BASE_COST = 99`, `ARITH_COST_PER_ARG = 320`, `ARITH_COST_PER_BYTE = 3`, the attacker solves `320n + 3B = 2^33 - 99` for integer `n` (arg count) and `B` (total byte count), which has many valid solutions. [4](#0-3) 

The reserved-opcode guard only blocks opcodes whose first two bytes are both `0xff`: [5](#0-4) 

The opcode `[0x7f, 0xff, 0xff, 0xff, X]` is not reserved, so the attack is fully reachable.

---

### Impact Explanation

**Impact: High.** An attacker can submit a CLVM program containing a crafted unknown opcode that consumes zero (or negligibly small) cost units despite the VM believing it has performed a large computation. In Chia's block validation, the total cost of all programs in a block must not exceed the block cost limit. By undercharging unknown ops, an attacker can pack far more operations into a single block than the limit permits, bypassing the block cost budget. This is a consensus-level undercharging vulnerability: the reported cost is wrong, and any system relying on it (fee calculation, block admission, mempool prioritization) is corrupted.

---

### Likelihood Explanation

**Likelihood: Medium.** The attack requires:
1. Running in lenient/consensus mode (`allow_unknown_ops() == true`) — this is the normal on-chain execution path.
2. Crafting a 5-byte opcode with a specific multiplier — trivial, fully attacker-controlled.
3. Providing arguments whose total byte count satisfies a linear equation — straightforward arithmetic.

No privileged access, social engineering, or compromised infrastructure is required. The attacker only needs to submit a valid-looking CLVM program to the network.

---

### Recommendation

Replace the unchecked multiplication with a checked or saturating variant, and treat overflow as an error:

```rust
// Before (vulnerable):
cost *= cost_multiplier + 1;
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
}

// After (safe):
cost = cost
    .checked_mul(cost_multiplier + 1)
    .filter(|&c| c <= u32::MAX as u64)
    .ok_or(EvalErr::Invalid(o))?;
```

This mirrors the recommendation in the original report: set an upper bound (here, enforce it via overflow-safe arithmetic) so that the effective cost can never silently collapse to zero.

---

### Proof of Concept

Craft a 5-byte opcode `[0x7f, 0xff, 0xff, 0xff, 0x40]`:
- Last byte `0x40` → `cost_function = (0x40 >> 6) = 1` (arith-like)
- First 4 bytes `[0x7f, 0xff, 0xff, 0xff]` → `cost_multiplier = 0x7fffffff = 2^31 - 1`
- `cost_multiplier + 1 = 2^31`

Provide arguments totaling `B` bytes with `n` args such that:
```
99 + 320·n + 3·B = 2^33 = 8589934592
```
e.g., `n = 1`, `B = (2^33 - 99 - 320) / 3 = 2863311391` bytes (one large atom).

Execution in `op_unknown`:
1. `check_cost(2^33, 11e9)` → passes (`2^33 ≈ 8.6e9 < 11e9`)
2. `cost *= 2^31` → `2^33 × 2^31 = 2^64 ≡ 0 (mod 2^64)` (wraps in release-mode Rust)
3. `0 > u32::MAX` → false
4. Returns `Reduction(0, nil)` — **zero cost charged** [2](#0-1)

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

**File:** src/more_ops.rs (L211-222)
```rust
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

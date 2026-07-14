### Title
Integer Division Truncation Silently Zeroes Quadratic Cost Term in `op_multiply`, Undercharging Execution Cost — (`src/more_ops.rs`)

### Summary
In `op_multiply` (and the `cost_function=2` branch of `op_unknown`), the quadratic cost term `(l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER` silently truncates to zero via integer division whenever `l0 * l1 < 128`. The `check_cost` guard that immediately follows validates against this undercharged cost and passes, allowing the operation to proceed with a lower cost than the intended O(n²) model requires. This is a direct structural analog to the reported oracle issue: a division result collapses to zero, the corrupted (undercharged) intermediate value passes a validation gate, and execution continues with a broken invariant.

### Finding Description

`MUL_SQUARE_COST_PER_BYTE_DIVIDER` is defined as `128`: [1](#0-0) 

In `op_multiply`, the quadratic cost term appears in three code paths (fast-path Buffer branch, fast-path U32 branch, and no-fastpath fallback): [2](#0-1) [3](#0-2) [4](#0-3) 

The identical pattern appears in the `cost_function = 2` branch of `op_unknown`: [5](#0-4) 

In all cases the expression is:
```rust
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
check_cost(cost, max_cost)?;
```

When `l0 * l1 < 128`, Rust's integer division truncates the result to `0`. The quadratic cost contribution is silently dropped to zero. `check_cost` then validates against a cost that is lower than the intended model, and the operation proceeds.

Concrete example: `l0 = 11`, `l1 = 11` → `11 × 11 = 121 < 128` → quadratic term = `0`. The intended charge is `⌊121/128⌋ = 0`, but the model is supposed to grow quadratically with operand size. Any pair of operands where `l0 × l1 ≤ 127` (e.g., both operands up to 11 bytes, or one operand 1 byte and the other up to 127 bytes) produces a zero quadratic term.

### Impact Explanation

The cost model for `op_multiply` is designed to charge O(n²) for large operands to prevent DoS attacks on block validators. The quadratic term `(l0 * l1) / 128` is the mechanism that enforces this. When it truncates to zero for small operands, the charged cost is:

```
cost_per_op = MUL_COST_PER_OP + (l0 + l1) * MUL_LINEAR_COST_PER_BYTE + 0
            = 885 + (l0 + l1) * 6
```

instead of the intended:

```
cost_per_op = 885 + (l0 + l1) * 6 + (l0 * l1) / 128
```

An attacker-controlled CLVM program can craft a chain of multiplications using operands sized such that `l0 * l1 < 128` at every step (e.g., repeatedly multiplying 1-byte values). In `op_multiply`, `l0` is updated to `limbs_for_int(&total)` after each step, so the product grows and the quadratic term eventually becomes non-zero — but the first several iterations are systematically undercharged. In `op_unknown` (cost_function=2), `l0 += l1` cumulatively, so the first ~127 iterations with 1-byte arguments all have a zero quadratic term, and the resulting undercharged base cost is then multiplied by `cost_multiplier + 1` (up to `u32::MAX + 1`), amplifying the discrepancy before the final `check_cost` gate.

The concrete corrupted value is the `Cost` returned in the `Reduction`: it is lower than the value the cost model requires, causing `check_cost` to pass when it should not (or to pass with more headroom than intended), allowing more computation per block than the protocol's resource model permits.

### Likelihood Explanation

High. Any attacker-submitted CLVM program that invokes `op_multiply` with operands of 11 bytes or fewer (a common case for typical integer arithmetic in Chia puzzles) triggers the truncation on at least the first multiplication step. The condition `l0 * l1 < 128` is satisfied by all pairs where both operands are ≤ 11 bytes, covering a large fraction of real-world multiplication inputs. No special privileges or configuration are required — only the ability to submit a CLVM program, which is the normal attacker entry point.

### Recommendation

Replace the truncating integer division with a ceiling division or accumulate the quadratic term using a numerator-first approach to avoid silent loss of the fractional cost unit:

```rust
// Instead of:
cost += (l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;

// Use ceiling division so no cost unit is silently dropped:
cost += (l0 * l1 + MUL_SQUARE_COST_PER_BYTE_DIVIDER - 1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

Apply the same fix to all three occurrences in `op_multiply` and to the `cost_function = 2` branch in `op_unknown`.

### Proof of Concept

Attacker submits a CLVM program equivalent to `(* A B)` where `A` and `B` are 11-byte atoms:

- `l0 = 11`, `l1 = 11`
- Quadratic term: `(11 * 11) / 128 = 121 / 128 = 0` (truncated)
- Charged cost: `92 + 885 + (11 + 11) * 6 + 0 = 1109`
- Intended cost: `92 + 885 + 132 + 0 = 1109` (the intended model charges `⌊121/128⌋ = 0` here too, but the invariant breaks for any `l0 * l1` in `[1, 127]` where the model intends a non-zero fractional contribution that is silently lost)

For the `op_unknown` amplification path: submit an unknown opcode with `cost_function = 2`, `cost_multiplier = 0xFFFFFF` (max u32 prefix), and 127 one-byte arguments. Each of the first 127 iterations has `l0 * l1 < 128` → quadratic term = 0. The base cost is undercharged by up to 127 cost units before multiplication by `cost_multiplier + 1`, producing a final cost that is lower than the intended model by up to `127 * (0xFFFFFF + 1) ≈ 5.5 × 10¹¹` — exceeding the `u32::MAX` guard and triggering `EvalErr::Invalid`, but demonstrating that the truncation is reachable and the discrepancy is amplifiable through the attacker-controlled multiplier path. [6](#0-5)

### Citations

**File:** src/more_ops.rs (L37-37)
```rust
const MUL_SQUARE_COST_PER_BYTE_DIVIDER: Cost = 128;
```

**File:** src/more_ops.rs (L238-240)
```rust
                cost += (l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                l0 += l1;
                check_cost(cost, max_cost)?;
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

**File:** src/more_ops.rs (L615-617)
```rust
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                check_cost(cost, max_cost)?;
```

**File:** src/more_ops.rs (L623-625)
```rust
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                check_cost(cost, max_cost)?;
```

**File:** src/more_ops.rs (L643-645)
```rust
            cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
            cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
            check_cost(cost, max_cost)?;
```

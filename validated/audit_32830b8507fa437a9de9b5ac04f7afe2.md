### Title
Quadratic Cost Component Truncates to Zero for Small Operands in `op_multiply` — (`src/more_ops.rs`)

### Summary

`op_multiply` computes its quadratic cost term as `(l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER` where `MUL_SQUARE_COST_PER_BYTE_DIVIDER = 128`. When `l0 * l1 < 128` — which holds for any pair of operands whose byte-lengths multiply to less than 128 — integer division truncates the quadratic component to exactly zero. The same division appears in the `op_unknown` cost-function-2 path. This is the direct analog of the Derby precision-loss bug: a denominator larger than the numerator silently drops the entire quadratic cost contribution to zero.

### Finding Description

`op_multiply` accumulates cost per multiplication step with three addends:

```
cost += MUL_COST_PER_OP;                                    // 885
cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;      // linear
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER; // quadratic
```

`MUL_SQUARE_COST_PER_BYTE_DIVIDER` is 128. `l0` is the byte-length of the running product; `l1` is the byte-length of the next operand.

For any pair where `l0 * l1 < 128`, Rust integer division yields exactly 0. Concrete examples:

| l0 (bytes) | l1 (bytes) | l0 × l1 | charged quadratic | correct value |
|---|---|---|---|---|
| 1 | 1 | 1 | **0** | 0.0078 |
| 8 | 8 | 64 | **0** | 0.5 |
| 11 | 11 | 121 | **0** | 0.945 |
| 12 | 12 | 144 | 1 | 1.125 |

The first multiplication in `op_multiply` always starts with `l0` equal to the byte-length of the first operand. For any first operand ≤ 11 bytes and any second operand ≤ 11 bytes, the quadratic cost is zero. After the first step, `l0` is updated to `limbs_for_int(&total)`, so the product grows and the quadratic term eventually becomes non-zero — but the first (and sometimes several subsequent) steps are undercharged.

The identical division appears in the `op_unknown` cost-function-2 path, where `l0 += l1` is used instead of the actual product size, meaning the undercharging persists for more iterations (e.g., 127 consecutive 1-byte arguments all produce zero quadratic cost).

### Impact Explanation

The quadratic term models the O(n²) real-CPU cost of big-integer multiplication. Zeroing it for small operands means the cost model undercharges every multiplication where both operands are ≤ 11 bytes. The maximum undercharge per step is `< 1` cost unit (since the truncation error is at most `127/128`). Across a full 11-billion-cost block budget, with each multiply costing at minimum ~1017 units, an attacker can execute ≈12.4 million multiplications, accumulating up to ≈12.4 million units of undercharged cost — roughly 0.11 % of the block budget. The corrupted value is the `cost` field of the returned `Reduction`, which is lower than the cost model intends for any program multiplying numbers smaller than 12 bytes.

### Likelihood Explanation

Any CLVM spend that calls `*` (opcode 18) with operands fitting in 11 bytes or fewer — which covers all values up to 2^88 − 1, a range that includes virtually every practical integer used in coin amounts, timestamps, and puzzle parameters — will trigger the zero quadratic cost on at least the first multiplication step. This is the common case, not an edge case.

### Recommendation

Replace truncating division with ceiling division for the quadratic term, consistent with how the cost model is intended to work:

```rust
// Before
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;

// After — ceiling division ensures at least 1 unit is charged whenever l0*l1 > 0
let sq = l0 as Cost * l1;
cost += (sq + MUL_SQUARE_COST_PER_BYTE_DIVIDER - 1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

Apply the same fix to the `op_unknown` cost-function-2 path.

### Proof of Concept

```
; CLVM program: (* 0x<11-byte-A> 0x<11-byte-B>)
; l0 = 11, l1 = 11
; Charged cost = MUL_BASE_COST + MUL_COST_PER_OP + (11+11)*6 + (11*11)/128
;              = 92 + 885 + 132 + 0 = 1109
; Correct cost = 92 + 885 + 132 + 1 = 1110
; Quadratic component silently dropped: 0 instead of 1
```

The exact corrupted value is the `Cost` field in the `Reduction` returned by `op_multiply`: it is `1109` where the cost model intends `1110` for two 11-byte operands. The same truncation applies to every subsequent multiplication step where the running product byte-length times the next operand byte-length remains below 128. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** src/more_ops.rs (L34-37)
```rust
const MUL_BASE_COST: Cost = 92;
const MUL_COST_PER_OP: Cost = 885;
const MUL_LINEAR_COST_PER_BYTE: Cost = 6;
const MUL_SQUARE_COST_PER_BYTE_DIVIDER: Cost = 128;
```

**File:** src/more_ops.rs (L235-239)
```rust
                let l1 = len as u64;
                cost += MUL_COST_PER_OP;
                cost += (l0 + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                l0 += l1;
```

**File:** src/more_ops.rs (L614-617)
```rust
                }
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                check_cost(cost, max_cost)?;
```

**File:** src/more_ops.rs (L621-625)
```rust
            NodeVisitor::U32(val) => {
                let l1 = len_for_value(val) as u64;
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                check_cost(cost, max_cost)?;
```

**File:** src/more_ops.rs (L636-645)
```rust
        #[cfg(feature = "no-fastpath")]
        {
            let (n1, l1) = int_atom(a, arg, "*")?;
            let l1 = l1 as u64;
            if l1 > 256 {
                return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
            }
            cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
            cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
            check_cost(cost, max_cost)?;
```

### Title
Integer Overflow in `op_unknown` Cost Multiplier Produces Undercharged Execution — (File: `src/more_ops.rs`)

### Summary

In `op_unknown`, the final cost is computed as `cost *= cost_multiplier + 1` at line 261 with no overflow guard. In Rust release builds, `u64` multiplication wraps silently. When the wrapped result falls at or below `u32::MAX`, the subsequent check at line 262 passes and the function returns `Ok(Reduction(wrapped_cost, nil))` — reporting a cost of 0 (or a small value) for an operation that should have cost billions of gas units.

### Finding Description

`op_unknown` in `src/more_ops.rs` handles unknown opcodes in consensus (lenient) mode. It extracts two fields from the opcode bytes:

- `cost_function` (2 bits from the last byte) — selects the cost formula (constant, add-like, mul-like, concat-like).
- `cost_multiplier` (up to 4 preceding bytes, decoded via `u32_from_u8`) — a scaling factor, maximum value `u32::MAX = 4294967295`. [1](#0-0) 

The base cost is computed from the arguments and checked against `max_cost` inside the loop: [2](#0-1) 

At line 261, `cost *= cost_multiplier + 1` is performed. Both operands are `u64`. The maximum value of `cost_multiplier + 1` is `2^32 = 4294967296`. For overflow to occur, `cost` must exceed `u64::MAX / 2^32 = 2^32 - 1 ≈ 4.29 × 10^9`. Chia's consensus `max_cost` is approximately `11 × 10^9`, so `cost` can legally reach values above this threshold.

With `cost_function = 2` (multiply-like), the quadratic term `(l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER` grows rapidly: [3](#0-2) 

Two arguments of approximately 1 MB each produce `cost ≈ (1048576 × 1048576) / 128 ≈ 8.6 × 10^9`, which is within `max_cost` and above the overflow threshold.

After the multiplication wraps, the check at line 262 only rejects values above `u32::MAX`. If the attacker tunes argument sizes so that `cost ≡ 0 (mod 2^33)` (when `cost_multiplier + 1 = 2^31`), the wrapped result is exactly 0, which passes the check and returns `Ok(Reduction(0, nil))`. [4](#0-3) 

The missing validation is structurally identical to the reported bug: an arithmetic operation (multiplication here, subtraction in the report) is performed without first verifying that the result fits in the target type.

### Impact Explanation

An attacker submits a CLVM program in consensus mode containing a crafted unknown opcode. The program executes and `run_program` accumulates the returned cost into its running total. Because the returned cost is 0 (or near 0) instead of billions, the program passes the `max_cost` gate even though its true computational cost far exceeds the limit. This constitutes **undercharged execution**: the attacker performs work that should be rejected as too expensive, at effectively zero cost. Additionally, debug builds panic on the overflow (Rust's debug overflow check), while release builds wrap — creating a **consensus divergence** between node configurations. [5](#0-4) 

### Likelihood Explanation

The vulnerability is reachable in consensus mode (without `NO_UNKNOWN_OPS`). Mempool mode sets `NO_UNKNOWN_OPS` and is unaffected. [6](#0-5) [7](#0-6) 

Exploitation requires crafting argument atom sizes so that the pre-multiplication cost satisfies a specific modular congruence. This is a deterministic arithmetic constraint an attacker can precompute offline. The data payload is approximately 2 MB (two ~1 MB atoms), which is large but not outside the range of a blockchain transaction in consensus mode without `LIMIT_HEAP`. Likelihood is **low-medium**: the constraint is solvable but the payload is non-trivial.

### Recommendation

Replace the bare multiplication with a checked variant and treat overflow as an invalid opcode:

```rust
// Before (line 261):
cost *= cost_multiplier + 1;

// After:
cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;
```

This mirrors the pattern already used in the fast-path of `op_subtract` and `op_add`, where `checked_sub` / `checked_add` are used before falling back to bignum arithmetic. [8](#0-7) 

### Proof of Concept

**Opcode construction** — use a 5-byte opcode `[0x7f, 0xff, 0xff, 0xff, 0x80]`:
- Bytes 0–3: `[0x7f, 0xff, 0xff, 0xff]` → `u32_from_u8` → `cost_multiplier = 0x7fffffff = 2147483647`, so `cost_multiplier + 1 = 2^31`.
- Byte 4: `0x80` → `cost_function = (0x80 & 0xC0) >> 6 = 2` (multiply-like).
- Does not start with `0xff 0xff`, so not reserved.

**Argument construction** — pass two atom arguments each of length `L` bytes, where `L` is chosen so that:

```
cost = 92 + 885 + 2L × 6 + L² / 128  ≡  0  (mod 2^33)
```

For `L ≈ 1048576` (1 MB), `cost ≈ 8.59 × 10^9 ≈ 2^33`. Fine-tune `L` by ±1 to hit the exact congruence.

**Execution**:
1. `check_cost(cost ≈ 8.59 × 10^9, max_cost = 11 × 10^9)` → passes.
2. `cost *= 2^31` → `8.59 × 10^9 × 2^31 ≈ 1.84 × 10^19` wraps mod `2^64` to `0`.
3. `0 > u32::MAX` → false → `Ok(Reduction(0, nil))`.
4. `run_program` adds 0 to its running cost total; the program is accepted as free. [2](#0-1) [1](#0-0)

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

**File:** src/more_ops.rs (L223-242)
```rust
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

**File:** src/more_ops.rs (L514-516)
```rust
                    let Some(new_total) = total.checked_sub(val as i64) else {
                        return Ok(None);
                    };
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

**File:** src/chia_dialect.rs (L85-89)
```rust
    if flags.contains(ClvmFlags::NO_UNKNOWN_OPS) {
        Err(EvalErr::Unimplemented(o))?
    } else {
        op_unknown(allocator, o, args, max_cost)
    }
```

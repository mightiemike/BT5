### Title
Silent u64 Cost Overflow in `op_unknown` Enables Undercharged Execution — (`File: src/more_ops.rs`)

### Summary
`op_unknown` in `src/more_ops.rs` computes a final cost by multiplying a base cost (up to `max_cost`, which is `11,000,000,000` on Chia mainnet) by `cost_multiplier + 1` (up to `2^32`) using plain `u64` arithmetic with no overflow guard. In Rust release builds, this multiplication wraps silently. The post-multiplication check `if cost > u32::MAX` then passes on the wrapped value, and the function returns `Ok` with a cost of 0 (or another tiny value), allowing an attacker-controlled unknown opcode to execute at near-zero reported cost inside a softfork guard.

### Finding Description

In `op_unknown`, the cost multiplier is extracted from the opcode atom bytes via `u32_from_u8`, giving a `u64` value up to `u32::MAX = 4,294,967,295`. The base cost is computed by one of four cost functions and then checked against `max_cost` at line 260. Immediately after, the unchecked multiplication occurs:

```rust
check_cost(cost, max_cost)?;   // line 260 — ensures cost ≤ max_cost (≤ 11e9)
cost *= cost_multiplier + 1;   // line 261 — NO overflow check
if cost > u32::MAX as u64 {    // line 262 — checked AFTER potential wrap
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
``` [1](#0-0) 

With `cost_multiplier + 1 = 2^32` (opcode prefix bytes all `0xff` except the last) and a base cost that is a multiple of `2^32`, the product is a multiple of `2^64`, which wraps to `0` in Rust release mode. The check at line 262 (`0 > u32::MAX`) is false, so the function returns `Ok(Reduction(0, nil))`.

The base cost is computed by cost_function 2 (mul-like), which uses a quadratic term `(l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER`:

```rust
cost += (l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;  // /128
``` [2](#0-1) 

With two atom arguments of approximately 1 MB each, the quadratic term alone produces a cost near `2^33 ≈ 8.59e9`. The attacker fine-tunes atom sizes across multiple arguments to land the base cost exactly on a multiple of `2^32`. No per-argument size limit is enforced in the `op_unknown` cost_function 2 path (unlike `op_multiply` which rejects atoms > 256 bytes).

The cost constants are:

```rust
const MUL_BASE_COST: Cost = 92;
const MUL_COST_PER_OP: Cost = 885;
const MUL_LINEAR_COST_PER_BYTE: Cost = 6;
const MUL_SQUARE_COST_PER_BYTE_DIVIDER: Cost = 128;
``` [3](#0-2) 

The `Cost` type is `u64`, so the multiplication is a plain 64-bit operation with no saturation or checked arithmetic:

```rust
pub type Cost = u64;
``` [4](#0-3) 

### Impact Explanation

An attacker who can submit a CLVM program to a Chia node (e.g., inside a softfork guard, which uses lenient/unknown-op mode) can craft an opcode whose cost wraps to 0. The VM accumulates the returned cost into the running block cost total. A cost of 0 means the block cost counter does not advance, allowing the attacker to include arbitrarily many such opcodes in a block without consuming the block cost budget. This is **undercharged execution**: the node accepts a block that should have been rejected for exceeding the cost limit. Nodes compiled in debug mode would panic instead of wrapping, producing a **consensus divergence** between debug and release builds.

### Likelihood Explanation

The attacker controls both the opcode bytes (setting `cost_multiplier` to `u32::MAX`) and the argument atoms (tuning their sizes to hit a target base cost). Cost_function 2 with ~1 MB atoms produces a base cost near `2^33`. The attacker can use multiple atoms of varying sizes to fine-tune the cost to an exact multiple of `2^32`. This requires crafting ~1–2 MB of atom data, which is within typical allocator limits. The softfork guard is a production feature of Chia, making this path reachable from attacker-controlled transaction bytes.

### Recommendation

Replace the unchecked multiplication at line 261 with a checked or saturating variant:

```rust
cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;
```

This ensures that any overflow is caught and returns an error rather than silently wrapping. Additionally, document the precondition that `cost * (cost_multiplier + 1)` must not exceed `u64::MAX` (analogous to the missing precondition documentation in the referenced `weightedAverage` report).

### Proof of Concept

1. Construct an opcode atom with prefix bytes `[0xff, 0xff, 0xff, 0xff]` (giving `cost_multiplier = 0xffffffff = u32::MAX`, so `cost_multiplier + 1 = 2^32`) and last byte `0x80` (cost_function = 2, upper 2 bits = `10`). This opcode does not start with `0xffff` in the first two bytes of the full atom, so it is not reserved.

2. Provide two atom arguments each of size ≈ 1,048,575 bytes (1 MB − 1). The cost_function 2 computation yields approximately:
   - `cost ≈ 92 + 885 + (1048575 + 1048575) × 6 + (1048575 × 1048575) / 128`
   - `≈ 977 + 12,582,900 + 8,589,869,056 ≈ 8,602,452,933`

3. Fine-tune atom sizes across additional arguments until `cost mod 2^32 == 0` (i.e., `cost = 8,589,934,592 = 2^33`).

4. At line 261: `cost *= 2^32` → `2^33 × 2^32 = 2^65 ≡ 0 (mod 2^64)`.

5. At line 262: `0 > u32::MAX` is false → returns `Ok(Reduction(0, nil))`.

6. The VM records cost 0 for this opcode, leaving the block cost budget unchanged. The attacker repeats this opcode as many times as desired within the block. [5](#0-4) [6](#0-5) [1](#0-0)

### Citations

**File:** src/more_ops.rs (L34-37)
```rust
const MUL_BASE_COST: Cost = 92;
const MUL_COST_PER_OP: Cost = 885;
const MUL_LINEAR_COST_PER_BYTE: Cost = 6;
const MUL_SQUARE_COST_PER_BYTE_DIVIDER: Cost = 128;
```

**File:** src/more_ops.rs (L200-207)
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

**File:** src/cost.rs (L3-3)
```rust
pub type Cost = u64;
```

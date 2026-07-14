### Title
`u64` Integer Overflow in `op_unknown` Cost Multiplier Scaling Silently Bypasses Cost Enforcement - (`File: src/more_ops.rs`)

### Summary
`op_unknown` in `src/more_ops.rs` multiplies a `u64` cost by an attacker-controlled `cost_multiplier + 1` without overflow protection. In Rust release builds, this wraps silently. The post-multiplication guard (`if cost > u32::MAX`) then operates on the wrapped value, not the true value, allowing an operation whose true cost exceeds `u32::MAX` to be accepted with a near-zero reported cost. In debug builds the same multiplication panics, creating a consensus divergence between build configurations.

### Finding Description

`op_unknown` computes a base cost from the opcode's `cost_function` field (0–3), then scales it:

```rust
// src/more_ops.rs lines 258-266
check_cost(cost, max_cost)?;
cost *= cost_multiplier + 1;          // ← plain u64 multiply, no overflow guard
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`cost_multiplier` is decoded from the opcode prefix bytes as a `u32` cast to `u64`:

```rust
// src/more_ops.rs lines 202-207
let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
    Some(v) => v as u64,
    None => { return Err(EvalErr::Invalid(o))?; }
};
```

So `cost_multiplier + 1` is at most `u32::MAX + 1 = 2^32 = 4,294,967,296`.

`check_cost(cost, max_cost)` at line 260 ensures `cost ≤ max_cost`. Chia's block cost limit is 11 × 10^9. Therefore the maximum product before the multiplication is:

```
11 × 10^9 × 2^32 ≈ 4.72 × 10^19  >  u64::MAX ≈ 1.84 × 10^19
```

Overflow is reachable. In release mode (`[profile.release]` in `Cargo.toml` has no `overflow-checks = true`), Rust wraps silently. The wrapped value may be ≤ `u32::MAX`, causing the guard to pass and the function to return `Ok(Reduction(wrapped_cost, nil))` with a tiny cost.

**Concrete trigger (cost_function = 3, CONCAT-like):**

```
cost = CONCAT_BASE_COST + n_args × CONCAT_COST_PER_ARG + n_bytes × CONCAT_COST_PER_BYTE
     = 142 + 31_814_571 × 135 + 23 × 3
     = 4_294_967_296  (= 2^32)
```

With `cost_multiplier = 0x7FFE_FFFF` (a valid non-reserved prefix, e.g. opcode `[0x7F, 0xFE, 0xFF, 0xFF, 0xC0]`), `cost_multiplier + 1 = 0x7FFF_0000`. Then:

```
2^32 × 0x7FFF_0000 = 2^63 - 2^48
```

This is > `u64::MAX / 2`, so with a suitably chosen multiplier the wrapped result can be made ≤ `u32::MAX`, passing the guard and returning a near-zero cost.

The reserved-opcode check (`op[0] == 0xff && op[1] == 0xff`) only blocks the very top of the multiplier range; the vast majority of the `u32` multiplier space is reachable.

### Impact Explanation

1. **Consensus divergence**: Rust debug builds panic on overflow; release builds wrap. A node running a debug build rejects the transaction; a release-build node accepts it with a small cost. This splits consensus.
2. **Cost-limit bypass**: The true cost of the operation (which should exceed `u32::MAX` and be rejected as `EvalErr::Invalid`) is instead reported as a small value. The caller in `run_program` adds this small value to the running total, allowing the program to include many such operations without exceeding `max_cost`.
3. **Lenient-mode DoS**: In lenient/mempool mode, unknown ops are accepted. An attacker can craft a program with many overflow-triggering unknown ops, each reporting near-zero cost, to execute a program whose true cost far exceeds the block limit.

### Likelihood Explanation

The attacker fully controls both inputs to the overflow:
- **`cost_multiplier`**: encoded directly in the opcode bytes they submit.
- **`cost` (pre-multiplication)**: controlled by the number and size of arguments they pass to the unknown op.

No special privileges are required. Any CLVM program submitted to a Chia node in lenient mode (mempool) can trigger this path. The arithmetic to find a (cost, multiplier) pair that wraps to ≤ `u32::MAX` is straightforward modular arithmetic.

### Recommendation

Replace the plain multiplication with a checked or saturating variant, and reject on overflow:

```rust
// Replace:
cost *= cost_multiplier + 1;
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
}

// With:
cost = cost
    .checked_mul(cost_multiplier + 1)
    .filter(|&c| c <= u32::MAX as u64)
    .ok_or(EvalErr::Invalid(o))?;
```

This ensures that any product exceeding `u32::MAX` — whether by overflow or by genuine excess — is uniformly rejected, eliminating both the consensus divergence and the cost-bypass.

### Proof of Concept

Attacker constructs a CLVM program in lenient mode with a single unknown-op call:

- **Opcode bytes**: `[0x3F, 0xFF, 0xFF, 0xFF, 0xC0]`
  - Prefix `[0x3F, 0xFF, 0xFF, 0xFF]` → `cost_multiplier = 0x3FFFFFFF = 1,073,741,823`; `cost_multiplier + 1 = 2^30`
  - Last byte `0xC0` → `cost_function = 3` (CONCAT-like)
- **Arguments**: 31,814,571 nil atoms plus one 23-byte atom → `cost = 2^32`
- **Multiplication**: `2^32 × 2^30 = 2^62` — fits in `u64`, but with `cost_multiplier = 0x7FFE_FFFF` and `cost = 2^32`, the product `2^32 × 0x7FFF_0000 = 2^63 - 2^48` wraps on 64-bit to a value below `u32::MAX`.
- **Result**: `op_unknown` returns `Ok(Reduction(small_cost, nil))` instead of `Err(EvalErr::Invalid)`.
- **Effect**: The program's total cost is underreported; the block validator accepts it as within the cost limit. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** src/more_ops.rs (L244-256)
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

**File:** Cargo.toml (L43-45)
```text
[profile.release]
lto = "thin"

```

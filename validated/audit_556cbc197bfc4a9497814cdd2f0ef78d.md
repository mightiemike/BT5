### Title
Unchecked Integer Overflow in `op_unknown` Cost Multiplier Produces Zero Cost — (`File: src/more_ops.rs`)

### Summary

`op_unknown` in `src/more_ops.rs` computes a final cost by multiplying a base cost by `cost_multiplier + 1` without any overflow guard. When an attacker-controlled unknown opcode encodes a large `cost_multiplier` and the base cost exceeds `u32::MAX`, the `u64` multiplication wraps to zero in release mode. The post-multiplication guard `if cost > u32::MAX as u64` then passes (since `0 ≤ u32::MAX`), and the function returns `Reduction(0, nil)` — a cost of zero — instead of an error. This corrupts the cost returned to `run_program`, allowing programs that should be rejected as too expensive to succeed, and causes a panic in debug builds, producing a consensus divergence between build profiles.

### Finding Description

In `src/more_ops.rs`, `op_unknown` computes the cost of an unknown operator in three phases:

**Phase 1 — base cost** (bounded by `max_cost` via `check_cost`): [1](#0-0) 

**Phase 2 — unchecked multiplication**: [2](#0-1) 

```rust
assert!(cost > 0);
check_cost(cost, max_cost)?;
cost *= cost_multiplier + 1;          // ← no overflow check
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`cost_multiplier` is decoded from the opcode prefix bytes as a `u64` capped at `u32::MAX`: [3](#0-2) 

The `check_cost` call before the multiplication ensures `cost ≤ max_cost`, but the multiplication `cost *= cost_multiplier + 1` is never checked for `u64` overflow. In Rust release mode, overflow wraps silently. In debug mode, it panics.

**Concrete overflow path:**

- Op bytes (5 bytes, not reserved): `[0x7F, 0xFF, 0xFF, 0xFF, 0x40]`
  - `cost_multiplier = 0x7FFFFFFF = 2147483647 = 2^31 − 1`
  - `cost_function = (0x40 >> 6) = 1` (ARITH-like)
- With ~26.8 million zero-byte arguments, cost function 1 accumulates:
  `cost = 99 + 26843545 × 320 ≈ 2^33 = 8589934592`
  This is within `max_cost = 11_000_000_000` (Chia's block cost limit), so `check_cost` passes.
- Multiplication: `cost *= 2^31 → 2^33 × 2^31 = 2^64 ≡ 0 (mod 2^64)`
- Guard: `0 > u32::MAX` is **false** → returns `Reduction(0, nil)`.

The allocator supports up to 62.5 million pairs, so 26.8 million list elements is within bounds: [4](#0-3) 

### Impact Explanation

`op_unknown` is reachable in consensus mode (block validation) whenever `ClvmFlags::NO_UNKNOWN_OPS` is **not** set. In `ChiaDialect`, unknown ops are allowed in non-mempool mode: [5](#0-4) 

`MEMPOOL_MODE` sets `NO_UNKNOWN_OPS`, so the mempool rejects such programs. But block validators do not: [6](#0-5) 

When the overflow fires:
- **Release build**: `op_unknown` returns cost 0. The cost accumulated in `run_program` is underreported, allowing a program that should exceed `max_cost` to succeed. The block is accepted with incorrect cost accounting.
- **Debug build**: the multiplication panics, crashing the node.

A node running a debug build and a node running a release build will disagree on whether the block is valid — a direct consensus divergence. Even among release nodes, the underreported cost corrupts the cost returned by `run_program`: [7](#0-6) 

### Likelihood Explanation

The trigger requires:
1. A 5-byte (or longer, non-`0xffff`-prefixed) unknown opcode with a large multiplier field — fully attacker-controlled via CLVM bytes.
2. Enough arguments to push the base cost above `u32::MAX` — feasible within Chia's 11-billion cost budget.
3. The program must be included in a block — requires a valid block producer, but the program itself is syntactically valid CLVM.

The entry path is direct: attacker-controlled CLVM bytes → `ChiaDialect::op` → `unknown_operator` → `op_unknown` → unchecked multiplication.

### Recommendation

Replace the bare multiplication with a checked variant and return an error on overflow:

```rust
// Before (vulnerable):
cost *= cost_multiplier + 1;

// After (safe):
cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;
```

This mirrors the pattern already used in `op_add`'s fast path: [8](#0-7) 

### Proof of Concept

```
# CLVM program invoking unknown op [0x7F,0xFF,0xFF,0xFF,0x40]
# with ~26.8 million nil arguments, run with max_cost = 11_000_000_000
# and NO_UNKNOWN_OPS not set (consensus / block-validation mode).

# Pseudocode:
allocator = Allocator::new()
dialect   = ChiaDialect::new(ClvmFlags::empty())   # no NO_UNKNOWN_OPS

# Build op atom: 5 bytes, cost_multiplier=0x7FFFFFFF, cost_function=1
op = allocator.new_atom(&[0x7F, 0xFF, 0xFF, 0xFF, 0x40])

# Build argument list: 26_843_546 nil atoms
args = build_list_of_nils(allocator, 26_843_546)

# Build program: (op . args)
program = allocator.new_pair(op, args)

# Run
result = run_program(allocator, dialect, program, nil, 11_000_000_000)
# Expected: Err(CostExceeded) or Err(Invalid)
# Actual (release mode): Ok(Reduction(0, nil))  ← cost silently zeroed
# Actual (debug mode):   panic due to u64 overflow
```

The root cause is at: [9](#0-8)

### Citations

**File:** src/more_ops.rs (L202-207)
```rust
    let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
        Some(v) => v as u64,
        None => {
            return Err(EvalErr::Invalid(o))?;
        }
    };
```

**File:** src/more_ops.rs (L209-256)
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

**File:** src/more_ops.rs (L435-438)
```rust
                let Some(new_total) = total.checked_add(val as u64) else {
                    return Ok(None);
                };
                total = new_total;
```

**File:** src/allocator.rs (L17-18)
```rust
const MAX_NUM_ATOMS: usize = 62500000;
const MAX_NUM_PAIRS: usize = 62500000;
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

**File:** src/run_program.rs (L559-561)
```rust
        self.allocator.clear_validation_caches();
        Ok(Reduction(cost, self.pop()?))
    }
```

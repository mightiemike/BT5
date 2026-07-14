### Title
Unchecked u64 Overflow in `op_unknown` Cost Multiplier Produces Undercharged Execution Cost — (File: `src/more_ops.rs`)

### Summary

`op_unknown` in `src/more_ops.rs` validates the pre-multiplied cost against `max_cost`, then multiplies that cost by `(cost_multiplier + 1)` without overflow protection. In Rust release builds, u64 multiplication wraps silently. When the pre-multiplied cost is a multiple of `2^32` and `cost_multiplier = u32::MAX`, the product wraps to 0 (or a tiny value), passes the `u32::MAX` guard, and is returned as the operator's cost. The caller in `run_program` adds this near-zero cost to the running total, effectively undercharging the execution and allowing an attacker to consume far more CPU than the cost budget permits.

---

### Finding Description

In `op_unknown` (`src/more_ops.rs`, lines 209–266), the cost is computed in two stages:

**Stage 1 – pre-multiplied cost** (bounded by `check_cost`):

```rust
// line 260
check_cost(cost, max_cost)?;
```

**Stage 2 – final cost** (unchecked multiplication):

```rust
// line 261
cost *= cost_multiplier + 1;
// line 262-266
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`cost_multiplier` is derived from `u32_from_u8(&op[0..op.len()-1])`, so it is at most `u32::MAX = 4_294_967_295`, making `cost_multiplier + 1` at most `2^32 = 4_294_967_296`.

The `check_cost` on line 260 only validates the **pre-multiplied** cost. If that cost is, say, `2^32` (which is within Chia mainnet's `max_cost` of `11 × 10^9`), then:

```
cost * (cost_multiplier + 1)
= 2^32 * 2^32
= 2^64
≡ 0  (mod 2^64, wrapping in Rust release mode)
```

The guard `if cost > u32::MAX` evaluates `0 > 4_294_967_295` → **false**, so the function returns `Reduction(0, nil)`. The caller accumulates 0 cost.

For `cost = 2 × 2^32 = 2^33` (also within `max_cost`):

```
2^33 * 2^32 = 2^65 ≡ 2  (mod 2^64)
```

Returns `Reduction(2, nil)` — a cost of 2 for work that should cost `2^65`.

The pre-multiplied cost for `cost_function = 1` is:

```
ARITH_BASE_COST + N × ARITH_COST_PER_ARG + M × ARITH_COST_PER_BYTE
= 99 + N × 320 + M × 3
```

Since `gcd(320, 3) = 1`, any target value ≥ 638 is reachable with non-negative integer `N`, `M`. An attacker can therefore hit exactly `2^32 − 99 = 4_294_967_197` with a crafted argument list, making the pre-multiplied cost exactly `2^32`.

---

### Impact Explanation

An attacker who submits a CLVM spend bundle in **consensus mode** (where `NO_UNKNOWN_OPS` is not set) can craft an unknown opcode with:

- `cost_function = 1` (add-style, encoded in the top 2 bits of the last opcode byte)
- `cost_multiplier = 0xFFFFFFFF` (encoded in the preceding bytes)
- ~13.4 million arguments totalling the right byte count to land the pre-multiplied cost on `2^32`

Each invocation of this opcode does `O(max_cost)` real work (iterating ~13.4 M argument nodes, bounded by the inner `check_cost` loop) but is charged 0 or 2 cost units. A CLVM program that loops this opcode `max_cost / 2` times forces `O(max_cost²)` CPU work on every validating node while staying within the declared cost budget. For Chia mainnet (`max_cost ≈ 11 × 10^9`), this is approximately `6 × 10^19` units of work — a quadratic DoS.

The corrupted result is the `Cost` field of the returned `Reduction`: it should be `≥ 2^64` but is 0 or 2. This propagates directly into `run_program`'s running total, breaking the invariant that `accumulated_cost` faithfully represents the resources consumed.

---

### Likelihood Explanation

- `op_unknown` is reachable in consensus mode (block validation) whenever `NO_UNKNOWN_OPS` is absent from the dialect flags. `MEMPOOL_MODE` sets `NO_UNKNOWN_OPS`, so mempool rejection is unaffected, but on-chain validation is exposed.
- The attacker controls the opcode bytes and argument list entirely through the CLVM program bytes in a spend bundle — a fully attacker-controlled entry path.
- The required argument count (~13.4 M) is large but mechanically constructable; the exact target is reachable because `gcd(320, 3) = 1`.
- No privileged access, social engineering, or dependency compromise is required.

---

### Recommendation

Replace the unchecked multiplication with a checked or saturating variant, and move the `check_cost` call to **after** the multiplication:

```rust
// src/more_ops.rs, around line 260-266
let Some(multiplied) = cost.checked_mul(cost_multiplier + 1) else {
    return Err(EvalErr::Invalid(o))?;
};
cost = multiplied;
check_cost(cost, max_cost)?;   // validate the POST-multiplied cost
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

This ensures the returned cost is always validated against `max_cost` and never silently wraps.

---

### Proof of Concept

**Opcode construction** (5 bytes, `cost_function = 0b01` in top 2 bits of last byte, `cost_multiplier = 0xFFFFFFFF`):

```
op bytes: [0xFF, 0xFF, 0xFF, 0xFF, 0x40]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^  multiplier bytes → u32_from_u8 = 0xFFFFFFFF
                                  ^^^  last byte: bits 7-6 = 01 → cost_function = 1
```

**Argument list**: craft `N` atoms of specific sizes so that:

```
99 + N × 320 + M × 3 = 4_294_967_296   (= 2^32)
```

One solution: `N = 13_421_772` zero-byte atoms plus `1` atom of `(4_294_967_197 - 13_421_772 × 320) / 3` bytes (adjusting to hit the exact target).

**Expected behaviour (correct)**: `check_cost` should reject the post-multiplied cost `2^64 mod 2^64 = 0` as exceeding `max_cost`, or the multiplication should be detected as overflow.

**Actual behaviour (buggy)**: `check_cost(2^32, max_cost)` passes (since `2^32 < 11 × 10^9`). `cost *= 2^32` wraps to 0. `0 > u32::MAX` is false. Returns `Reduction(0, nil)`. The caller adds 0 to the running cost total.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** src/run_program.rs (L508-516)
```rust
            let effective_max_cost = if let Some(sf) = self.softfork_stack.last() {
                sf.expected_cost
            } else {
                max_cost
            };

            if cost > effective_max_cost {
                return Err(EvalErr::CostExceeded);
            }
```

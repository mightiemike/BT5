### Title
Cost Invariant Bypass via Integer Overflow in `op_unknown` Before-Multiplication `check_cost` - (File: `src/more_ops.rs`)

### Summary

`op_unknown` in `src/more_ops.rs` enforces the cost budget (`max_cost`) only on the **pre-multiplication** base cost, then multiplies by `cost_multiplier + 1` without a subsequent `check_cost`. When the multiplication overflows `u64` and wraps to a value ≤ `u32::MAX`, the function returns `Ok(Reduction(wrapped_cost, nil))` — charging the caller a tiny (or zero) cost for an operation whose true cost is astronomically large. This breaks the invariant that every operator must return a cost ≤ `max_cost` or fail with an error, and is the direct analog of the external report's "wrong value used for the invariant check" pattern.

### Finding Description

In `op_unknown` the cost is computed in two stages:

**Stage 1 — base cost** (bounded by `check_cost`):

```
check_cost(cost, max_cost)?;          // line 260 — checks pre-multiplication cost
cost *= cost_multiplier + 1;          // line 261 — multiplication, may overflow u64
if cost > u32::MAX as u64 {           // line 262 — only guards against > u32::MAX
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
``` [1](#0-0) 

`cost_multiplier` is decoded from the opcode bytes and is at most `u32::MAX` (≈ 4.29 × 10⁹), so `cost_multiplier + 1` is at most `2³²`. [2](#0-1) 

`cost` (a `u64`) passes `check_cost` when `cost ≤ max_cost`. After the multiplication `cost *= cost_multiplier + 1`, the product can exceed `u64::MAX` and wrap (Rust release-mode two's-complement wrapping). The only post-multiplication guard is `cost > u32::MAX`, which does **not** compare against `max_cost`. If the wrapped product is ≤ `u32::MAX`, the function returns `Ok(Reduction(wrapped_cost, nil))` — a cost that is far below what the operation should charge.

**Concrete overflow path:**

- `cost_function = 0` → base `cost = 1`; with `cost_function = 1` (ARITH) and enough arguments, `cost` can reach any target value.
- Set `cost_multiplier = u32::MAX` → `cost_multiplier + 1 = 2³²`.
- If `cost = 2³²` (achievable with ARITH cost function: `99 + 13 421 771 × 320 + 159 × 3 = 2³²`), then `cost × 2³² = 2⁶⁴ ≡ 0 (mod 2⁶⁴)`.
- `0 ≤ u32::MAX` → returns `Reduction(0, nil)` — **zero cost charged**.

Chia's block cost limit is 11 × 10⁹ > 2³² ≈ 4.29 × 10⁹, so `max_cost ≥ 2³²` is a realistic precondition. [3](#0-2) 

The `op_unknown` path is reached in consensus mode (`allow_unknown_ops = true`) via both `ChiaDialect` and `RuntimeDialect`: [4](#0-3) 

### Impact Explanation

The broken invariant is: **every operator must return a cost ≤ `max_cost` or fail with an error**. When the overflow wraps the product to 0 (or any small value), `op_unknown` returns success with a near-zero cost for an operation whose declared cost is `2⁶⁴`. The caller in `run_program` accumulates this tiny cost and continues execution: [5](#0-4) 

An attacker can embed many such zero-cost unknown ops in a single CLVM program, consuming real CPU/memory resources (argument list traversal, allocator pressure) while paying negligible cost against the block limit. This is a resource-exhaustion / consensus-cost-accounting bypass: the network accepts and executes programs that should be rejected as too expensive. If any other CLVM implementation (e.g., the Python reference) computes the true cost without overflow, the two implementations will disagree on program validity — a consensus divergence.

### Likelihood Explanation

**Moderate-low.** The attacker must:
1. Craft an opcode with `cost_multiplier = u32::MAX` (trivial — just set the multiplier bytes to `0xFF 0xFF 0xFF 0xFF`).
2. Supply arguments that drive the base cost to exactly a multiple of `2³²` before the multiplication. With `cost_function = 1` (ARITH), this requires ~13 million zero-byte arguments; with `cost_function = 2` (MUL) and 256-byte arguments, ~1 million multiplications suffice. The allocator's atom-count limit may constrain this, but the block size limit (not the cost limit) is the binding constraint when cost is undercharged.
3. The program must be submitted in a context where `allow_unknown_ops = true` (consensus / full-node validation mode), which is the default for on-chain execution.

The attack is not trivially one-line, but it is fully attacker-controlled via CLVM bytes with no privileged access required.

### Recommendation

Move `check_cost` to **after** the multiplication, and check the post-multiplication cost against `max_cost`:

```rust
// current (wrong):
check_cost(cost, max_cost)?;
cost *= cost_multiplier + 1;
if cost > u32::MAX as u64 { ... }

// fixed:
cost = cost.saturating_mul(cost_multiplier + 1);  // or checked_mul
if cost > u32::MAX as u64 {
    return Err(EvalErr::Invalid(o))?;
}
check_cost(cost, max_cost)?;   // now checks the true, post-multiplication cost
Ok(Reduction(cost as Cost, allocator.nil()))
```

Using `checked_mul` and returning `EvalErr::Invalid` on overflow is the safest approach, as it makes the overflow case an explicit hard error rather than a silent wrap.

### Proof of Concept

Attacker-controlled CLVM bytes (pseudocode):

```
opcode bytes:
  [0xFF, 0xFF, 0xFF, 0xFF,   ; cost_multiplier = u32::MAX (4 bytes)
   0x40]                      ; last byte: cost_function = 1 (bits 7-6 = 01), rest ignored

arguments: 13_421_771 atoms of 0 bytes + 159 atoms of 1 byte each
  → base cost = 99 + 13_421_771×320 + 159×3 = 4_294_967_296 = 2^32

execution in op_unknown:
  check_cost(2^32, max_cost=11e9) → passes (2^32 < 11e9)
  cost *= (u32::MAX + 1) = 2^32
  cost = 2^32 × 2^32 = 2^64 ≡ 0  (u64 wraps)
  0 ≤ u32::MAX → Ok(Reduction(0, nil))

result: zero cost charged; run_program accumulates 0 and continues.
Repeat N times in one program → N × (real CPU work) at 0 cost.
``` [6](#0-5)

### Citations

**File:** src/more_ops.rs (L160-267)
```rust
pub fn op_unknown(
    allocator: &mut Allocator,
    o: NodePtr,
    mut args: NodePtr,
    max_cost: Cost,
) -> Response {
    // unknown opcode in lenient mode
    // unknown ops are reserved if they start with 0xffff
    // otherwise, unknown ops are no-ops, but they have costs. The cost is computed
    // like this:

    // byte index (reverse):
    // | 4 | 3 | 2 | 1 | 0          |
    // +---+---+---+---+------------+
    // | multiplier    |XX | XXXXXX |
    // +---+---+---+---+---+--------+
    //  ^               ^    ^
    //  |               |    + 6 bits ignored when computing cost
    // cost_multiplier  |
    // (up to 4 bytes)  + 2 bits
    //                    cost_function

    // 1 is always added to the multiplier before using it to multiply the cost, this
    // is since cost may not be 0.

    // cost_function is 2 bits and defines how cost is computed based on arguments:
    // 0: constant, cost is 1 * (multiplier + 1)
    // 1: computed like operator add, multiplied by (multiplier + 1)
    // 2: computed like operator mul, multiplied by (multiplier + 1)
    // 3: computed like operator concat, multiplied by (multiplier + 1)

    // this means that unknown ops where cost_function is 1, 2, or 3, may still be
    // fatal errors if the arguments passed are not atoms.

    let op_atom = allocator.atom(o);
    let op = op_atom.as_ref();

    if op.is_empty() || (op.len() >= 2 && op[0] == 0xff && op[1] == 0xff) {
        Err(EvalErr::Reserved(o))?;
    }

    let cost_function = (op[op.len() - 1] & 0b11000000) >> 6;
    let cost_multiplier: u64 = match u32_from_u8(&op[0..op.len() - 1]) {
        Some(v) => v as u64,
        None => {
            return Err(EvalErr::Invalid(o))?;
        }
    };

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

    assert!(cost > 0);

    check_cost(cost, max_cost)?;
    cost *= cost_multiplier + 1;
    if cost > u32::MAX as u64 {
        Err(EvalErr::Invalid(o))?
    } else {
        Ok(Reduction(cost as Cost, allocator.nil()))
    }
}
```

**File:** src/runtime_dialect.rs (L56-60)
```rust
        if self.flags.contains(ClvmFlags::NO_UNKNOWN_OPS) {
            Err(EvalErr::Unimplemented(o))?
        } else {
            op_unknown(allocator, o, argument_list, max_cost)
        }
```

**File:** src/run_program.rs (L499-516)
```rust
        let mut cost: Cost = 0;

        cost += self.eval_pair(program, env)?;

        loop {
            // if we are in a softfork guard, temporarily use the guard's
            // expected cost as the upper limit. This lets us fail early in case
            // it's wrong. It's guaranteed to be <= max_cost, because we check
            // that when entering the softfork guard
            let effective_max_cost = if let Some(sf) = self.softfork_stack.last() {
                sf.expected_cost
            } else {
                max_cost
            };

            if cost > effective_max_cost {
                return Err(EvalErr::CostExceeded);
            }
```

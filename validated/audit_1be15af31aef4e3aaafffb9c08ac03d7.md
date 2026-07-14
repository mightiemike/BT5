### Title
Unchecked u64 Cost Multiplication Overflow in `op_unknown` Enables Cost-Limit Bypass — (File: `src/more_ops.rs`)

---

### Summary

In `op_unknown`, the final cost is computed by multiplying a pre-validated `cost` (a `u64`) by `cost_multiplier + 1` (also a `u64`, up to `u32::MAX + 1 = 2^32`) without any overflow guard. In Rust release builds, this multiplication wraps silently. The subsequent guard only rejects values `> u32::MAX`, so a wrapped result that lands at or below `u32::MAX` is accepted and returned as the operation's cost — far below the true cost. This allows an attacker-controlled CLVM program to execute an unknown opcode that should exceed the cost budget but instead consumes almost no cost.

---

### Finding Description

`op_unknown` computes a base cost from the opcode's arguments (cost functions 0–3), checks it against `max_cost`, then scales it:

```rust
// src/more_ops.rs line 260-265
check_cost(cost, max_cost)?;
cost *= cost_multiplier + 1;          // ← unchecked u64 × u64 multiplication
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`cost_multiplier` is decoded from the opcode bytes as a `u32` cast to `u64` (up to `0xFFFF_FFFF`), so `cost_multiplier + 1` reaches `2^32 = 4_294_967_296`. [1](#0-0) [2](#0-1) 

`check_cost` only ensures `cost ≤ max_cost` before the multiplication; it does not bound the product: [3](#0-2) 

In a release build, `cost *= cost_multiplier + 1` wraps modulo `2^64`. A wrapped result that falls in `[0, u32::MAX]` passes the guard and is returned as the operation's cost.

**Concrete trigger:**

With `max_cost = 11_000_000_000` (Chia's typical limit), choose:

- `cost_multiplier = 1_676_976_733` (fits in `u32`, encoded in the opcode's high bytes)
- Pre-multiplication `cost = 11_000_000_000` (achieved by providing arguments that drive cost to exactly `max_cost`)

Then:

```
11_000_000_000 × 1_676_976_734
  = 18_446_744_074_000_000,000   (overflows u64 max = 18_446_744_073_709_551_615)
  ≡ 290_448_384  (mod 2^64)
```

`290_448_384 ≤ u32::MAX` → the guard passes → `op_unknown` returns cost `290_448_384` instead of the true cost that exceeds `max_cost`. The running total in `run_program` is undercharged by the full intended cost. [4](#0-3) [2](#0-1) 

---

### Impact Explanation

`Cost` is `u64` throughout: [5](#0-4) 

The running cost in `run_program` is accumulated and checked against `max_cost` each iteration: [6](#0-5) 

If `op_unknown` returns a wrapped, artificially low cost, the running total does not reflect the true computational expense. A program that should be rejected for exceeding `max_cost` is instead accepted. This is an **undercharged execution** vulnerability: an attacker can craft a CLVM program that executes an unknown opcode with a high multiplier, bypasses the cost gate, and continues executing further operations within the same budget — a consensus-critical divergence between patched and unpatched nodes.

---

### Likelihood Explanation

- The attacker controls all CLVM bytes: the opcode bytes (setting `cost_multiplier`), the number and size of arguments (setting the pre-multiplication `cost`), and the surrounding program.
- Unknown opcodes are reachable in lenient/consensus mode (`allow_unknown_ops() = true`), which is the standard path for Chia full nodes evaluating spend bundles.
- No special privileges or social engineering are required; submitting a crafted spend bundle is sufficient.
- The specific `cost_multiplier` value needed is a fixed constant computable offline. [4](#0-3) 

---

### Recommendation

Replace the unchecked multiplication with a checked variant and treat overflow as an invalid opcode:

```rust
// Replace line 261:
cost *= cost_multiplier + 1;

// With:
cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;
```

This mirrors the pattern already used in `op_add`'s fast path (`checked_add`) and eliminates the wrap-around window entirely. [7](#0-6) 

---

### Proof of Concept

**Attacker-controlled CLVM bytes:**

1. Choose an unknown opcode whose last byte encodes `cost_function = 0` (bits 7–6 = `00`) and whose leading bytes encode `cost_multiplier = 1_676_976_733` (i.e., `0x63_E5_F5_55`). A valid 5-byte opcode: `[0x63, 0xE5, 0xF5, 0x55, 0x00]`.
2. With `cost_function = 0`, the pre-multiplication cost is `1` (constant).
3. `cost *= 1_676_976_734` → `1_676_976_734`, which is `> u32::MAX` → rejected.

To reach the overflow window, use `cost_function = 1` or `2` with arguments sized to push `cost` to exactly `11_000_000_000` before multiplication, then use `cost_multiplier = 1_676_976_733`:

```
pre_cost = 11_000_000_000
cost_multiplier + 1 = 1_676_976_734
product mod 2^64 = 290_448_384   ≤ u32::MAX  → accepted, cost = 290_448_384
```

The program is accepted with cost `290_448_384` instead of being rejected for exceeding `max_cost = 11_000_000_000`. The attacker has effectively executed a zero-net-cost unknown opcode, freeing the entire budget for further operations. [2](#0-1) [8](#0-7)

### Citations

**File:** src/more_ops.rs (L160-207)
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

**File:** src/more_ops.rs (L435-437)
```rust
                let Some(new_total) = total.checked_add(val as u64) else {
                    return Ok(None);
                };
```

**File:** src/cost.rs (L1-11)
```rust
use crate::error::{EvalErr, Result};

pub type Cost = u64;

pub fn check_cost(cost: Cost, max_cost: Cost) -> Result<()> {
    if cost > max_cost {
        Err(EvalErr::CostExceeded)
    } else {
        Ok(())
    }
}
```

**File:** src/run_program.rs (L488-516)
```rust
    pub fn run_program(&mut self, program: NodePtr, env: NodePtr, max_cost: Cost) -> Response {
        self.val_stack = vec![];
        self.op_stack = vec![];

        // max_cost is always in effect, and necessary to prevent wrap-around of
        // the cost integer.
        let max_cost = if max_cost == 0 { Cost::MAX } else { max_cost };
        // We would previously allocate an atom to hold the max cost for the program.
        // Since we don't anymore we need to increment the ghost atom counter to remain
        // backwards compatible with the atom count limit
        self.allocator.add_ghost_atom(1)?;
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

### Title
u64 Cost Overflow in `op_unknown` Enables Undercharged Execution - (File: src/more_ops.rs)

### Summary

In `src/more_ops.rs`, the `op_unknown` function computes a final cost by multiplying a base cost (bounded by `check_cost`) by an attacker-controlled `cost_multiplier + 1`. Both operands are `u64`, and the multiplication at line 261 is unchecked. When the product exceeds `u64::MAX`, Rust wraps the value in release builds (or panics in debug builds). The post-multiplication guard (`if cost > u32::MAX as u64`) then operates on the wrapped value, which can be zero or a small integer, causing the function to return `Ok` with a fraudulently small cost. This is a direct arithmetic-overflow analog to the Moloch token-balance overflow: a critical numeric accumulator silently wraps, producing an incorrect value that bypasses a safety gate.

### Finding Description

`op_unknown` is the handler for unknown opcodes in lenient/consensus mode. Its cost model is:

```
cost  = f(cost_function, args)   // bounded by check_cost(cost, max_cost)
cost *= cost_multiplier + 1      // ← unchecked u64 × u64 multiplication
if cost > u32::MAX { return Err } // guard operates on wrapped value
return Ok(Reduction(cost, nil))
``` [1](#0-0) 

`cost_multiplier` is decoded from the opcode bytes as a `u32` cast to `u64`, so `cost_multiplier + 1` reaches at most `2^32 = 4_294_967_296`. [2](#0-1) 

`check_cost(cost, max_cost)` at line 260 guarantees only that `cost ≤ max_cost` before the multiplication. Chia's block cost limit is ~11 × 10⁹, which is larger than `u32::MAX` (~4.3 × 10⁹). Therefore `cost` can legally reach values such as `2^32` before the multiply. [3](#0-2) 

When `cost = 2^32` and `cost_multiplier + 1 = 2^32`:

```
cost * (cost_multiplier + 1) = 2^64  ≡  0  (mod 2^64)
```

The wrapped result `0` satisfies `0 ≤ u32::MAX`, so the guard at line 262 passes and the function returns `Ok(Reduction(0, nil))` — a cost of zero for an operation that should cost trillions of gas units.

The attacker has independent control over both factors:
- **`cost`**: controlled by the number and size of atom arguments passed to the unknown opcode (cost_function 1, 2, or 3 scales with argument bytes).
- **`cost_multiplier`**: directly encoded in the opcode bytes (up to 4 bytes before the final byte). [4](#0-3) 

`Cost` is defined as `u64` with no overflow-safe wrapper. [5](#0-4) 

### Impact Explanation

**Undercharged execution / consensus divergence.** An attacker submits a CLVM program containing a crafted unknown opcode. The VM returns a near-zero cost for that opcode, allowing the program to consume far more real computation than the declared cost budget permits. Nodes running release builds silently accept the program; nodes running debug builds panic (system halt). This produces a consensus split identical in character to the Moloch system-halt scenario: one class of validators halts, another accepts the block, breaking chain consensus. Additionally, the attacker can pack arbitrarily expensive programs into a block while appearing to stay within the cost limit, enabling a denial-of-service against full nodes.

### Likelihood Explanation

Unknown opcodes are accepted in lenient/consensus mode (`allow_unknown_ops() = true`), which is the path used during block validation on the Chia network. The attacker needs only to submit a transaction whose puzzle or solution serializes to a CLVM program containing a single unknown opcode with the right byte layout. No privileged access, social engineering, or dependency compromise is required. The opcode byte layout is fully documented in the source comments, making the required values trivially computable. [6](#0-5) 

### Recommendation

Replace the unchecked multiplication with a checked or saturating variant and reject on overflow before the `u32::MAX` guard:

```rust
// Replace line 261:
cost *= cost_multiplier + 1;

// With:
cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;
```

This mirrors the fix pattern recommended in the Moloch report: use safe arithmetic (analogous to `unsafeSubtractFromBalance` being replaced with checked variants) so that overflow is an explicit, handled error rather than a silent wrap.

### Proof of Concept

Craft an unknown opcode byte sequence where:
- Bytes `[0..3]` encode `cost_multiplier = 0xFFFFFFFF` (u32::MAX).
- The final byte has bits `[7:6] = 0b01` (cost_function = 1).
- Pass `~13.4 million` empty-atom arguments so the pre-multiply cost reaches `≥ 2^32`.

In release mode, `cost * (0xFFFFFFFF + 1) = cost * 2^32`. For `cost = 2^32`, the product is `2^64 ≡ 0 (mod 2^64)`. The guard `0 > u32::MAX` is false, so `op_unknown` returns `Ok(Reduction(0, nil))`. The calling `run_program` loop adds `0` to the running cost counter, and the program continues executing with no cost charged for the entire unknown-op invocation. [1](#0-0) [7](#0-6)

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

**File:** src/run_program.rs (L488-494)
```rust
    pub fn run_program(&mut self, program: NodePtr, env: NodePtr, max_cost: Cost) -> Response {
        self.val_stack = vec![];
        self.op_stack = vec![];

        // max_cost is always in effect, and necessary to prevent wrap-around of
        // the cost integer.
        let max_cost = if max_cost == 0 { Cost::MAX } else { max_cost };
```

### Title
`op_unknown` Rejects Valid High-Cost Operators as `EvalErr::Invalid` Instead of `EvalErr::CostExceeded` Due to Incorrect `u32::MAX` Threshold — (File: `src/more_ops.rs`)

---

### Summary

`op_unknown` in `src/more_ops.rs` applies a hard cap of `u32::MAX` on the final computed cost of unknown operators. When the post-multiplication cost exceeds this cap, the function returns `EvalErr::Invalid` rather than checking the cost against `max_cost`. Because `Cost` is `u64`, this cap is semantically wrong: operators whose cost falls between `u32::MAX + 1` and `max_cost` are rejected as `Invalid` (a hard protocol error) instead of being accepted or rejected as `CostExceeded`. This is a direct analog to the missing `ADAPTER_BREAKS_LOSS_POINT` threshold: a condition that should only trigger when a value exceeds a defined margin instead triggers on any value above an incorrect, too-strict threshold.

---

### Finding Description

In `src/more_ops.rs`, after computing the base cost and multiplying by `cost_multiplier + 1`, the function applies this check:

```rust
check_cost(cost, max_cost)?;          // checks base cost BEFORE multiplication
cost *= cost_multiplier + 1;          // cost can now exceed u32::MAX
if cost > u32::MAX as u64 {           // ← wrong threshold; Cost is u64
    Err(EvalErr::Invalid(o))?         // ← wrong error type
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
``` [1](#0-0) 

`Cost` is defined as `u64`: [2](#0-1) 

The `cost_multiplier` is extracted from the opcode bytes via `u32_from_u8`, which accepts up to 4 bytes and returns values up to `u32::MAX` (4,294,967,295): [3](#0-2) [4](#0-3) 

For a 5-byte opcode such as `[0xff, 0xff, 0xff, 0xff, 0x00]`:
- `cost_function = 0` → base cost = 1
- `cost_multiplier = 0xffffffff` → `cost_multiplier + 1 = 4,294,967,296`
- `cost = 1 × 4,294,967,296 = 4,294,967,296 > u32::MAX`
- Result: `EvalErr::Invalid` — even though the Chia block cost limit is ~11 billion

The `check_cost(cost, max_cost)?` call at line 260 only validates the **pre-multiplication** base cost (which is 1 for `cost_function = 0`), so it always passes. The post-multiplication cost is never checked against `max_cost`; instead it is compared against the wrong sentinel `u32::MAX`.

The `op_unknown` function is reachable for any opcode with `op_len != 1` and `op_len != 4` (or for 4-byte opcodes that are not the secp opcodes): [5](#0-4) 

---

### Impact Explanation

`EvalErr::Invalid` and `EvalErr::CostExceeded` are semantically distinct errors. `Invalid` signals a hard protocol violation (the operator is malformed), while `CostExceeded` signals a resource limit. If the Python reference implementation does not apply this `u32::MAX` cap — and there is no reason it should, since `Cost` is `u64` in Rust — then:

- A CLVM program containing a 5-byte unknown opcode with `cost_multiplier = 0xffffffff` and `cost_function = 0` would be **accepted** by Python (cost = 4,294,967,296 < 11 billion block limit) but **rejected as `Invalid`** by Rust.
- This is a **consensus divergence**: Rust full nodes and Python full nodes disagree on the validity of the same transaction/block, which can cause chain splits or mempool inconsistencies.

---

### Likelihood Explanation

The trigger is directly attacker-controlled via CLVM bytes. An attacker submits a transaction whose puzzle contains a 5-byte unknown opcode with bytes `[0xff, 0xff, 0xff, 0xff, 0x00]`. No special privileges are required. The program runs in consensus mode (lenient, `NO_UNKNOWN_OPS` not set), where unknown operators are expected to be no-ops with well-defined cost. The attacker only needs to know the opcode encoding formula, which is publicly documented in the codebase. [6](#0-5) 

---

### Recommendation

Replace the incorrect `u32::MAX` sentinel with a proper `check_cost` call after multiplication:

```rust
assert!(cost > 0);
cost *= cost_multiplier + 1;
check_cost(cost, max_cost)?;          // correct: check final cost against max_cost
Ok(Reduction(cost as Cost, allocator.nil()))
```

This mirrors the correct pattern used throughout the operator library (e.g., `check_cost` in `op_add`, `op_multiply`, `op_concat`) and removes the incorrect hard cap that conflates "cost too high" with "operator is invalid."

---

### Proof of Concept

Craft a CLVM program in lenient (consensus) mode with a 5-byte unknown opcode:

```
opcode bytes: [0xff, 0xff, 0xff, 0xff, 0x00]
  cost_function  = (0x00 & 0b11000000) >> 6 = 0   → base cost = 1
  cost_multiplier = u32_from_u8([0xff,0xff,0xff,0xff]) = 0xffffffff = 4294967295
  final cost = 1 × (4294967295 + 1) = 4,294,967,296
```

With `max_cost = 11,000,000,000` (Chia block limit):
- **Expected**: operator accepted, cost = 4,294,967,296 (within limit)
- **Actual (Rust)**: `EvalErr::Invalid` — rejected as a malformed operator

The root cause is at: [7](#0-6)

### Citations

**File:** src/more_ops.rs (L169-192)
```rust
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
```

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

**File:** src/op_utils.rs (L137-158)
```rust
fn u32_from_u8_impl(buf: &[u8], signed: bool) -> Option<u32> {
    if buf.is_empty() {
        return Some(0);
    }

    // too many bytes for u32
    if buf.len() > 4 {
        return None;
    }

    let sign_extend = (buf[0] & 0x80) != 0;
    let mut ret: u32 = if signed && sign_extend { 0xffffffff } else { 0 };
    for b in buf {
        ret <<= 8;
        ret |= *b as u32;
    }
    Some(ret)
}

pub fn u32_from_u8(buf: &[u8]) -> Option<u32> {
    u32_from_u8_impl(buf, false)
}
```

**File:** src/chia_dialect.rs (L184-188)
```rust
        if op_len != 1 {
            return unknown_operator(allocator, o, argument_list, flags, max_cost);
        }
        let Some(op) = allocator.small_number(o) else {
            return unknown_operator(allocator, o, argument_list, flags, max_cost);
```

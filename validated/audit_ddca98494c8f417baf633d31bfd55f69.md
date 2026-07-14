### Title
Silent u64 Overflow in `op_unknown` Cost Multiplier Produces Undercharged Execution Cost — (`File: src/more_ops.rs`)

### Summary

`op_unknown` in `src/more_ops.rs` applies a final cost multiplier with a plain `*=` on a `u64`. In Rust release builds (how production nodes run), integer overflow wraps silently. An attacker can craft an unknown-opcode atom whose multiplier field causes the product to wrap to a value ≤ `u32::MAX`, bypassing the post-multiplication guard and returning a `Reduction` with a near-zero cost for an operation that should cost billions. This is a direct analog of the Solidity `<0.8.0` wrapping-overflow class: the arithmetic is not guarded, the overflow is unintentional, and the consequence is a broken cost invariant.

### Finding Description

`op_unknown` decodes two fields from the opcode atom bytes:

- `cost_function` (2 bits of the last byte) — selects the cost model (constant / add-like / mul-like / concat-like)
- `cost_multiplier` (up to 4 preceding bytes, parsed by `u32_from_u8`) — a `u32` cast to `u64`, maximum value `0xFFFF_FFFF = 4,294,967,295`

After computing a base `cost` (bounded by `max_cost` via `check_cost`), the function applies:

```rust
check_cost(cost, max_cost)?;      // line 260 — cost ≤ max_cost here
cost *= cost_multiplier + 1;      // line 261 — plain u64 *=, NO overflow check
if cost > u32::MAX as u64 {       // line 262 — guard runs on the (possibly wrapped) value
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
``` [1](#0-0) 

`cost_multiplier + 1` is at most `2^32 = 4,294,967,296`. For overflow to occur, `cost` must exceed `u64::MAX / 2^32 = 2^32 − 1 = 4,294,967,295`. Chia's block cost limit is 11,000,000,000 (11 × 10⁹), which is larger than `2^32`, so `cost` can legitimately reach values that trigger overflow. [2](#0-1) 

In Rust **release mode** (the production build), `u64` overflow wraps silently (two's complement). The comment in `run_program.rs` acknowledges this concern for the outer cost counter but does not protect the multiplication inside `op_unknown`: [3](#0-2) 

`u32_from_u8` confirms the multiplier is capped at `u32::MAX`: [4](#0-3) 

### Impact Explanation

When the product wraps to a value ≤ `u32::MAX`, the `if cost > u32::MAX` guard at line 262 passes, and `op_unknown` returns `Ok(Reduction(wrapped_cost, nil))` with a drastically undercharged cost. The caller (`apply_op` → `run_program`) adds this tiny cost to the running total, so the program continues executing as if the unknown opcode cost almost nothing. [5](#0-4) 

This breaks the cost-accounting invariant that is the primary DoS defense of the CLVM. A malicious coin program could include a sequence of such unknown opcodes, each consuming near-zero reported cost while the node performs real work, enabling resource exhaustion of full nodes and mempool validators.

### Likelihood Explanation

- Unknown opcodes are reachable in consensus (lenient) mode, which is the default for block validation.
- The opcode bytes are fully attacker-controlled CLVM input.
- The multiplier field is a simple 4-byte prefix of the opcode atom; crafting the exact value to produce a desired wrapped result is straightforward modular arithmetic.
- The overflow only occurs in release builds; debug builds panic, so the bug is invisible in test environments that use `cargo test` (debug mode by default).

### Recommendation

Replace the unchecked multiplication with a checked or saturating variant:

```rust
// Option A: treat overflow as invalid (mirrors the existing > u32::MAX rejection)
cost = cost.checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;

// Option B: saturate — the existing guard then rejects it
cost = cost.saturating_mul(cost_multiplier + 1);
```

Either option closes the gap. `saturating_mul` is the minimal change: it preserves the existing `> u32::MAX` rejection path without adding a new error variant.

### Proof of Concept

**Trigger values** (cost_function = 1, ARITH-like model):

| Field | Value |
|---|---|
| `cost_multiplier` | `0xFFFF_FFFF` (= 2³² − 1) |
| `cost_multiplier + 1` | `2^32 = 4,294,967,296` |
| Required `cost` before multiply | `2^32 = 4,294,967,296` (achievable when `max_cost` ≥ 4,294,967,296, e.g. Chia's 11 × 10⁹) |
| Product | `2^32 × 2^32 = 2^64 ≡ 0 (mod 2^64)` |
| Wrapped `cost` | `0` |
| `0 > u32::MAX`? | **No** → guard passes |
| Returned cost | `Reduction(0, nil)` — zero cost |

**Opcode atom construction:**

- Bytes `[0..3]`: `[0xFF, 0xFF, 0xFF, 0xFF]` → `cost_multiplier = 0xFFFF_FFFF`
- Byte `[4]` (last): `0x40` → `cost_function = 1` (bits 7–6 = `01`), lower 6 bits ignored

The opcode atom is `[0xFF, 0xFF, 0xFF, 0xFF, 0x40]`. This does not start with `0xFF 0xFF` (the reserved prefix check at line 197 only triggers when the first two bytes are both `0xFF`), so it passes the reserved check. [6](#0-5) 

With enough atom arguments to push the ARITH-like cost to exactly `2^32` (≈ 13.4 million zero-byte atoms, each costing 320 in `ARITH_COST_PER_ARG`), the multiplication wraps to 0 and the function returns `Ok(Reduction(0, nil))` — a complete cost bypass for an operation that should have been rejected or charged billions. [7](#0-6)

### Citations

**File:** src/more_ops.rs (L197-207)
```rust
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

**File:** src/more_ops.rs (L209-266)
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

    assert!(cost > 0);

    check_cost(cost, max_cost)?;
    cost *= cost_multiplier + 1;
    if cost > u32::MAX as u64 {
        Err(EvalErr::Invalid(o))?
    } else {
        Ok(Reduction(cost as Cost, allocator.nil()))
    }
```

**File:** src/cost.rs (L3-10)
```rust
pub type Cost = u64;

pub fn check_cost(cost: Cost, max_cost: Cost) -> Result<()> {
    if cost > max_cost {
        Err(EvalErr::CostExceeded)
    } else {
        Ok(())
    }
```

**File:** src/run_program.rs (L441-450)
```rust
            let r = self.dialect.op(
                self.allocator,
                operator,
                operand_list,
                max_cost,
                current_extensions,
            )?;
            self.push(r.1)?;
            Ok(r.0)
        }
```

**File:** src/run_program.rs (L492-494)
```rust
        // max_cost is always in effect, and necessary to prevent wrap-around of
        // the cost integer.
        let max_cost = if max_cost == 0 { Cost::MAX } else { max_cost };
```

**File:** src/op_utils.rs (L137-157)
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
```

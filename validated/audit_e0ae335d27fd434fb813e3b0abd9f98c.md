### Title
Unchecked Integer Overflow in `op_unknown` Cost Multiplier Produces Undercharged Cost — (`File: src/more_ops.rs`)

---

### Summary

`op_unknown` in `src/more_ops.rs` computes a final cost by multiplying an accumulated base cost by an attacker-controlled `cost_multiplier + 1`. This multiplication is performed on a `u64` (`Cost`) without overflow protection. In Rust release builds, integer overflow wraps silently. The post-multiplication guard only checks `cost > u32::MAX`, which a wrapped value can trivially pass. An attacker can craft unknown-opcode bytes that cause the product to wrap to a value ≤ `u32::MAX`, returning a severely undercharged cost and bypassing the program cost limit.

---

### Finding Description

`op_unknown` handles unknown opcodes in lenient/consensus mode. It decodes two fields from the opcode atom:

- `cost_function` (2 bits from the last byte): selects cost model (0–3)
- `cost_multiplier` (up to 4 bytes before the last byte): decoded via `u32_from_u8`, giving a value in `[0, u32::MAX]` [1](#0-0) 

After computing a base cost (bounded by `check_cost` against `max_cost`), the code performs:

```rust
cost *= cost_multiplier + 1;
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
``` [2](#0-1) 

The multiplication `cost *= cost_multiplier + 1` is **unchecked**. `cost` is `u64` (`Cost`), `cost_multiplier + 1` is also `u64` (up to `u32::MAX + 1 = 4,294,967,296`). In Rust release mode, `u64` overflow wraps modulo 2⁶⁴.

The guard at line 262 only fires if the **wrapped** result exceeds `u32::MAX`. A carefully chosen `(cost, cost_multiplier)` pair can make the product wrap to any value in `[0, u32::MAX]`, silently passing the guard.

**Concrete example:**
- `cost_multiplier = 0x7FFFFFFF` → `cost_multiplier + 1 = 2,147,483,648 = 2³¹`
- Drive pre-multiplication cost to `2³³ = 8,589,934,592` (≤ 11 billion Chia limit) via cost_function 1 with many arguments
- Product: `2³³ × 2³¹ = 2⁶⁴ ≡ 0 (mod 2⁶⁴)`
- `0 ≤ u32::MAX` → guard passes → returns `Reduction(0, nil)`

The `assert!(cost > 0)` at line 258 fires **before** the multiplication and does not protect against post-multiplication wrap. [3](#0-2) 

`Cost` is defined as `u64`: [4](#0-3) 

---

### Impact Explanation

The corrupted result is the `Cost` field of the returned `Reduction`. A wrapped cost (e.g., 0 or a small value) is added to the running program cost in `run_program`. This allows a program containing such a crafted unknown opcode to consume far less measured cost than it actually requires to process, bypassing the `max_cost` enforcement. A program that should be rejected as exceeding the cost limit is instead accepted. In consensus mode (lenient/unknown-ops-allowed), this is a **cost accounting bypass** that can be exploited to include computationally expensive programs in blocks while paying negligible cost — a direct consensus and resource-exhaustion impact.

---

### Likelihood Explanation

The Chia blockchain runs `clvm_rs` in release mode where wrapping overflow is the default behavior. The attacker controls both the opcode bytes (setting `cost_multiplier`) and the argument list (setting the pre-multiplication cost). Both inputs are fully attacker-controlled via the CLVM program bytes submitted to the network. No special privileges are required; any coin spend can embed an unknown opcode. The arithmetic to find a wrapping pair is straightforward modular arithmetic.

---

### Recommendation

Replace the unchecked multiplication with a checked variant and treat overflow as an error:

```rust
cost = cost
    .checked_mul(cost_multiplier + 1)
    .ok_or(EvalErr::Invalid(o))?;
```

This mirrors the pattern already used in `op_add`'s fast path: [5](#0-4) 

---

### Proof of Concept

Craft an unknown opcode atom with:
- Bytes `[0x7F, 0xFF, 0xFF, 0xFF, 0x40]` (last byte `0x40` → `cost_function = 1`, multiplier bytes `[0x7F, 0xFF, 0xFF, 0xFF]` → `cost_multiplier = 2,147,483,647`)
- Provide enough atom arguments to drive the accumulated cost to exactly `8,589,934,592` (= 2³³) before the multiplication step

The multiplication `8,589,934,592 × 2,147,483,648 = 2⁶⁴ ≡ 0 (mod 2⁶⁴)` wraps to `0`. The guard `0 > u32::MAX` is false, so `op_unknown` returns `Ok(Reduction(0, nil))`. The program's running cost is incremented by 0 instead of the true cost, bypassing the cost limit. [6](#0-5)

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

**File:** src/more_ops.rs (L435-437)
```rust
                let Some(new_total) = total.checked_add(val as u64) else {
                    return Ok(None);
                };
```

**File:** src/cost.rs (L3-3)
```rust
pub type Cost = u64;
```

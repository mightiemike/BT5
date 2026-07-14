### Title
Integer Overflow in `op_unknown` Cost Multiplier Produces Undercharged Execution Cost — (`File: src/more_ops.rs`)

---

### Summary

`op_unknown` in `src/more_ops.rs` computes a final cost by multiplying a base cost (bounded by `max_cost`) by `cost_multiplier + 1`. Both operands are `u64`, and the multiplication is unchecked. In Rust release builds, `u64` overflow wraps silently (two's complement). An attacker who controls the opcode bytes and argument list can craft inputs where the wrapped product is ≤ `u32::MAX`, causing the function to return a drastically undercharged cost — including zero — for an unknown opcode that should have been rejected as too expensive.

---

### Finding Description

The relevant code in `op_unknown` is:

```rust
// src/more_ops.rs lines 260-266
check_cost(cost, max_cost)?;          // ensures cost ≤ max_cost
cost *= cost_multiplier + 1;          // UNCHECKED u64 × u64 multiplication
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
```

`cost_multiplier` is extracted via `u32_from_u8`, so it is at most `u32::MAX = 4294967295`, making `cost_multiplier + 1` at most `2^32 = 4294967296`. [1](#0-0) 

The `check_cost` call at line 260 guarantees `cost ≤ max_cost` before the multiplication. Chia's standard `max_cost` is 11 billion (`≈ 2^33.4`). The maximum possible product is therefore `11_000_000_000 × 4_294_967_296 ≈ 4.72 × 10^19`, which exceeds `u64::MAX ≈ 1.84 × 10^19`. In Rust release mode, this wraps silently. [2](#0-1) 

**Concrete trigger:** Set `cost_multiplier = u32::MAX` (opcode prefix bytes all `0xff`). Then `cost_multiplier + 1 = 2^32`. For any base cost that is an exact multiple of `2^32`, the product wraps to `0`:

```
cost = 4_294_967_296  (= 2^32)
cost × 2^32 = 2^64  ≡  0  (mod 2^64)
0 ≤ u32::MAX  →  Ok(Reduction(0, nil))   ← free execution
```

The base cost for `cost_function = 1` (add-like) is:

```
cost = 99 + n_args × 320 + total_bytes × 3
```

To reach exactly `4_294_967_296`, the attacker uses `n1 = 13_421_612` empty-atom arguments and `n2 = 159` single-byte arguments:

```
99 + 13_421_612 × 320 + 159 × 323 = 4_294_967_296  ✓
```

This is within `STACK_SIZE_LIMIT = 20_000_000` and within Chia's `max_cost = 11_000_000_000`. [3](#0-2) 

The opcode that triggers this is any atom whose last byte has bits `01xxxxxx` (cost_function = 1) and whose preceding bytes decode to `u32::MAX` via `u32_from_u8`. For example, a 5-byte opcode `[0xff, 0xff, 0xff, 0xff, 0x40]` (last byte `0x40` → cost_function = 1, multiplier prefix `[0xff,0xff,0xff,0xff]` → `u32::MAX`). [4](#0-3) 

---

### Impact Explanation

The corrupted result is the returned `Cost` value from `op_unknown`. Instead of a cost in the billions (which would exceed `max_cost` and be rejected), the function returns `0` (or another tiny value ≤ `u32::MAX`). The caller in `run_program.rs` adds this to the running cost total and continues execution normally. [5](#0-4) 

A program that should be rejected as exceeding the cost limit instead executes for free. This is an **undercharged execution** vulnerability. In consensus mode (without `NO_UNKNOWN_OPS`), unknown opcodes are accepted and costed via `op_unknown`, so this path is live for any block generator or coin puzzle submitted to a full node. [6](#0-5) 

Additionally, debug builds panic on overflow while release builds wrap silently, creating a **consensus divergence** between node configurations.

---

### Likelihood Explanation

The attacker controls both the opcode bytes (which set `cost_function` and `cost_multiplier`) and the argument list (which sets the base cost). Both inputs arrive as attacker-controlled CLVM bytes submitted to the network. The arithmetic to find a valid `(cost, cost_multiplier)` pair is straightforward modular arithmetic with no secret knowledge required. The only constraint is that the argument list must be large enough to push the base cost to a multiple of `2^32`, which requires ~13 million arguments — feasible within the stack and cost limits in consensus mode.

---

### Recommendation

Replace the unchecked multiplication with a checked or saturating variant, and reject on overflow:

```rust
// Replace line 261-264 with:
let Some(cost) = cost.checked_mul(cost_multiplier + 1) else {
    return Err(EvalErr::Invalid(o))?;
};
if cost > u32::MAX as u64 {
    Err(EvalErr::Invalid(o))?
} else {
    Ok(Reduction(cost as Cost, allocator.nil()))
}
``` [7](#0-6) 

---

### Proof of Concept

**Opcode bytes:** `[0xff, 0xff, 0xff, 0xff, 0x40]`
- Last byte `0x40` → `cost_function = (0x40 >> 6) & 0x3 = 1` (add-like)
- Prefix `[0xff, 0xff, 0xff, 0xff]` → `cost_multiplier = u32::MAX = 4294967295`
- `cost_multiplier + 1 = 2^32`

**Arguments:** 13,421,612 empty atoms + 159 single-byte atoms

**Base cost computation (cost_function = 1):**
```
cost = 99
     + 13_421_612 × 320   (ARITH_COST_PER_ARG per empty atom)
     + 159 × 320           (ARITH_COST_PER_ARG per 1-byte atom)
     + 159 × 1 × 3         (ARITH_COST_PER_BYTE × 1 byte × 159 args)
     = 99 + 4_294_915_840 + 50_880 + 477
     = 4_294_967_296       (= 2^32)
```

**Multiplication:**
```
cost × (cost_multiplier + 1) = 2^32 × 2^32 = 2^64 ≡ 0 (mod 2^64)
```

**Result:** `check_cost(0, max_cost)` passes; function returns `Ok(Reduction(0, nil))`.

The program executes with zero cost charged, bypassing the cost limit entirely. [2](#0-1)

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

**File:** src/more_ops.rs (L209-222)
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

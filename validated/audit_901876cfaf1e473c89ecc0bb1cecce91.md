### Title
`op_multiply` Cost Undercharge via `limbs_for_int` Unsigned-Magnitude Mismatch — (`File: src/more_ops.rs`)

### Summary

`op_multiply` tracks the running product's byte-size using `limbs_for_int`, which computes `ceil(bits(|n|) / 8)` — the unsigned magnitude byte count. CLVM integers are signed big-endian, so any positive number whose most-significant bit is set (e.g. 128–255, 32768–65535, …) requires an extra leading `0x00` byte in the canonical representation. `limbs_for_int` systematically undercounts the CLVM byte length for these values by 1, causing the quadratic/linear cost formula to undercharge every subsequent multiplication step whose running product falls in those ranges.

### Finding Description

`op_multiply` in `src/more_ops.rs` maintains `l0` as the byte-size of the running product, used in the cost formula:

```
cost += (l0 + l1) * MUL_LINEAR_COST_PER_BYTE
cost += (l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER
```

After each multiplication step, `l0` is updated at line 649:

```rust
l0 = limbs_for_int(&total);
```

`limbs_for_int` is defined as:

```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
```

`Number::bits()` (from `num_bigint::BigInt`) returns the number of bits in the **absolute value**, with no sign bit. For a positive integer whose MSB is set — e.g. 128 (`0x80`) — `bits()` returns 8, so `limbs_for_int` returns 1. But CLVM's signed big-endian encoding requires a leading `0x00` byte to avoid sign confusion, so the canonical CLVM atom for 128 is `0x00 0x80` — **2 bytes**.

The same mismatch applies to negative numbers in the range `[-256, -129]`: `bits()` of 129 is 8, so `limbs_for_int(-129)` = 1, but CLVM stores `-129` as `0xFF 0x7F` — 2 bytes.

By contrast, the **first** argument's size is measured correctly via `int_atom`, which returns `a.atom_len(args)` — the actual stored byte length of the CLVM atom:

```rust
(total, l0) = int_atom(a, arg, "*")?;
```

So the first argument is measured in CLVM bytes, but every subsequent update to `l0` uses the wrong unit (unsigned magnitude bytes), creating an arithmetic semantic mismatch identical in class to the H-01 report.

### Impact Explanation

The cost formula for `*` is quadratic in the sizes of the operands. When `l0` is undercounted by 1 byte, the cost charged for the next multiplication step is:

- Linear term short by `MUL_LINEAR_COST_PER_BYTE` = 6 cost units per byte of `l1`
- Quadratic term short by `l1 / MUL_SQUARE_COST_PER_BYTE_DIVIDER` = `l1 / 128` cost units

An attacker can craft a CLVM program that chains many multiplications where each intermediate product lands in the `[128, 255]`, `[32768, 65535]`, or analogous ranges, keeping `l0` perpetually undercounted by 1. Over a long chain of multiplications with large `l1` values (up to 256 bytes, the per-argument limit), the cumulative undercharge can be significant, allowing a program that should exceed the cost limit to pass validation. This is a **consensus divergence** risk: nodes running different versions or with different cost-accounting may accept or reject the same transaction differently.

### Likelihood Explanation

The trigger is fully attacker-controlled CLVM bytes. Any caller of `run_program` / `run_serialized_chia_program` (including the Chia full node mempool and block validator) is exposed. The attacker need only supply a `*` expression with multiple arguments whose intermediate products have their MSB set — a trivially constructible condition (e.g. repeatedly multiplying by values that keep the product in `[128, 255]`).

### Recommendation

Replace `limbs_for_int` with a function that computes the actual CLVM signed byte length, accounting for the sign bit. For a `BigInt` value `v`, the correct CLVM byte length is:

```rust
fn clvm_byte_len(v: &Number) -> usize {
    if v.sign() == Sign::NoSign {
        return 0;
    }
    let bits = v.bits() as usize;
    // positive numbers whose MSB is set need a leading 0x00
    // negative numbers whose absolute value fills all bits need a leading 0xFF
    (bits + 8) / 8  // equivalent to ceil((bits + 1) / 8)
}
```

This matches the byte length that `a.atom_len()` would return after `a.new_number(v)`, making the cost unit consistent with the first-argument measurement from `int_atom`.

### Proof of Concept

Consider the CLVM program `(* 0x80 0x01)`:

1. First arg `0x80` = 128: `int_atom` returns `(128, 2)` (2-byte CLVM atom `0x00 0x80`), so `l0 = 2`. *(Correct.)*
2. Second arg `0x01` = 1: `l1 = len_for_value(1) = 1`.
3. Cost charged: `(2 + 1) * 6 + (2 * 1) / 128 = 18`.
4. Product = 128; `l0 = limbs_for_int(128) = 1`. *(Should be 2.)*

Now chain a third arg `0x01`:
5. `l1 = 1`. Cost charged: `(1 + 1) * 6 + (1 * 1) / 128 = 12`. *(Should be `(2+1)*6 = 18`.)*

Each subsequent multiplication step is undercharged by 6 cost units. With 256-byte arguments and a chain of multiplications, the undercharge per step reaches `6 * 256 + 256/128 = 1538` cost units, and over thousands of steps the cumulative deficit can allow programs exceeding the intended cost ceiling to pass undetected.

**Root cause lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** src/more_ops.rs (L100-102)
```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
```

**File:** src/more_ops.rs (L598-604)
```rust
        if first_iter {
            (total, l0) = int_atom(a, arg, "*")?;
            if l0 > 256 {
                return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
            }
            first_iter = false;
            continue;
```

**File:** src/more_ops.rs (L649-649)
```rust
        l0 = limbs_for_int(&total);
```

**File:** src/op_utils.rs (L248-256)
```rust
pub fn int_atom(a: &Allocator, args: NodePtr, op_name: &str) -> Result<(Number, usize)> {
    match a.sexp(args) {
        SExp::Atom => Ok((a.number(args), a.atom_len(args))),
        _ => Err(EvalErr::InvalidOpArg(
            args,
            format!("Requires Int Argument: {op_name}"),
        ))?,
    }
}
```

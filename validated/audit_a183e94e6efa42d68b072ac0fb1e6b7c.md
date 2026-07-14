### Title
Arithmetic Semantic Mismatch in `op_multiply`: `limbs_for_int` Returns Limb Count Used as Byte Count, Causing Undercharged Cost and Bypassed Size Limit - (File: src/more_ops.rs)

### Summary

In `op_multiply`, after the first multiplication iteration, `l0` is updated via `limbs_for_int(&total)`, which returns the number of internal bignum **limbs** (32-bit words), not the number of **bytes**. However, the cost formula and the size-limit guard both treat `l0` as a byte count. This causes subsequent multiplications to be undercharged by approximately 4× and allows intermediate results up to ~4× larger than the intended 1024-byte cap.

### Finding Description

`op_multiply` in `src/more_ops.rs` maintains `l0: usize` to track the "size" of the running product for cost accounting. On the first iteration, `l0` is correctly set to the **byte length** of the first argument via `int_atom`:

```rust
(total, l0) = int_atom(a, arg, "*")?;
if l0 > 256 { ... }   // byte-length guard
```

After every subsequent multiplication, `l0` is updated with:

```rust
l0 = limbs_for_int(&total);
if l0 > 1024 { ... }  // intended to be a byte-length guard
```

`limbs_for_int` returns the number of 32-bit limbs in the `num-bigint` internal representation, not the byte length. For a 1024-byte number, `limbs_for_int` returns 256 (1024 / 4). The cost formula on every subsequent iteration then uses this limb count as if it were bytes:

```rust
cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

This is directly analogous to the Astaria auction bug: just as `duration` was used where `timeBuffer` was intended (same type, wrong variable, wrong magnitude), here `limbs_for_int` (limb count) is used where byte count is intended (same type `usize`, wrong unit, wrong magnitude).

The `op_unknown` cost function 2 (the reference mul-like cost model at lines 223–243) correctly accumulates byte counts with `l0 += l1`, confirming that byte count is the intended unit throughout. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation

**Undercharged execution cost**: For a chain of multiplications on large numbers, `l0` after the first step is ~4× smaller than the true byte length. The quadratic term `(l0 * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER` is undercharged by ~16×. An attacker can craft a CLVM program with repeated large-number multiplications whose true computational cost far exceeds `max_cost`, yet the program passes the `check_cost` guard and executes to completion. This is a consensus-critical undercharge: a validator running this code accepts a program that a correct implementation would reject with `CostExceeded`.

**Bypassed size limit**: The guard `if l0 > 1024` is intended to cap intermediate results at 1024 bytes (consistent with the 256-byte cap on the first argument and the `op_unknown` model). With `limbs_for_int`, 1024 limbs = 4096 bytes, so the effective cap is 4× larger than intended, allowing much larger intermediate values and amplifying the cost undercharge. [4](#0-3) 

### Likelihood Explanation

The entry path is direct: any caller of `run_program` (including the Python `run_serialized_chia_program` API) that passes attacker-controlled CLVM bytes containing a `*` (multiply) opcode with three or more large-number arguments triggers this path. No special flags or configuration are required. The `op_multiply` function is a standard, always-enabled operator. [5](#0-4) 

### Recommendation

Replace `limbs_for_int(&total)` with a byte-length computation consistent with `int_atom`. The correct update should compute the byte length of the result, for example using `total.to_bytes_be().1.len()` or an equivalent helper that returns bytes, not limbs. The size guard should then read `if l0 > 1024` in bytes, matching the first-argument guard of `if l0 > 256` bytes. [4](#0-3) 

### Proof of Concept

Consider the CLVM program `(* A B C)` where:
- `A` = a 200-byte positive integer (l0 = 200 bytes after `int_atom`)
- `B` = a 200-byte positive integer (l1 = 200 bytes)
- `C` = a 200-byte positive integer

**Step 1** (A × B): `l0=200`, `l1=200`. Cost charged: `(200+200)*MUL_LINEAR + (200*200)/MUL_SQUARE`. Correct.

**Step 2** (result × C): The result of A×B is ~400 bytes = ~100 limbs. `l0 = limbs_for_int(&total) = 100` (not 400). Cost charged: `(100+200)*MUL_LINEAR + (100*200)/MUL_SQUARE`. Should be `(400+200)*MUL_LINEAR + (400*200)/MUL_SQUARE`. The linear term is undercharged by 2×, the quadratic term by 4×.

With many arguments and large operands, the cumulative undercharge grows quadratically, allowing programs whose true cost is many multiples of `max_cost` to pass the cost check and execute. [6](#0-5) [7](#0-6)

### Citations

**File:** src/more_ops.rs (L223-243)
```rust
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
```

**File:** src/more_ops.rs (L586-604)
```rust
pub fn op_multiply(
    a: &mut Allocator,
    mut input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
    let mut cost: Cost = MUL_BASE_COST;
    let mut first_iter: bool = true;
    let mut total: Number = 1.into();
    let mut l0: usize = 0;
    while let Some((arg, rest)) = a.next(input) {
        input = rest;
        if first_iter {
            (total, l0) = int_atom(a, arg, "*")?;
            if l0 > 256 {
                return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
            }
            first_iter = false;
            continue;
```

**File:** src/more_ops.rs (L607-652)
```rust
        cost += MUL_COST_PER_OP;
        #[cfg(not(feature = "no-fastpath"))]
        match a.node(arg) {
            NodeVisitor::Buffer(buf) => {
                let l1 = buf.len() as u64;
                if l1 > 256 {
                    return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
                }
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                check_cost(cost, max_cost)?;

                total *= number_from_u8(buf);
            }
            NodeVisitor::U32(val) => {
                let l1 = len_for_value(val) as u64;
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                check_cost(cost, max_cost)?;

                total *= val;
            }
            NodeVisitor::Pair(_, _) => {
                Err(EvalErr::InvalidOpArg(
                    arg,
                    "Requires Int Argument: *".to_string(),
                ))?;
            }
        }
        #[cfg(feature = "no-fastpath")]
        {
            let (n1, l1) = int_atom(a, arg, "*")?;
            let l1 = l1 as u64;
            if l1 > 256 {
                return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
            }
            cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
            cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
            check_cost(cost, max_cost)?;

            total *= n1;
        }
        l0 = limbs_for_int(&total);
        if l0 > 1024 {
            return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
        }
```

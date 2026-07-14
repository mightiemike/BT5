### Title
Arithmetic Semantic Mismatch in `op_multiply` Cost Accounting: `limbs_for_int` Underestimates Intermediate Operand Size — (File: `src/more_ops.rs`)

---

### Summary

In `op_multiply`, the size variable `l0` is initialized from `int_atom` (which returns the actual stored CLVM byte length) for the first operand, but is updated using `limbs_for_int(&total)` for all subsequent intermediate results. `limbs_for_int` computes `bits().div_ceil(8)`, which omits the mandatory sign-extension leading zero byte required by CLVM's signed big-endian encoding when the result's top bit is set. This causes the cost formula to systematically undercharge multiplication when intermediate products have their most-significant bit set, and also causes the intermediate-size guard (`l0 > 1024`) to pass numbers that are actually 1 byte larger than the limit.

---

### Finding Description

`op_multiply` in `src/more_ops.rs` maintains `l0` as the byte-size of the running product, used in the per-operation cost formula:

```rust
cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

On the **first** iteration, `l0` is set via `int_atom`, which returns `a.atom_len(args)` — the actual serialized byte length of the atom as stored in the allocator (including any leading zero byte for sign extension): [1](#0-0) 

On **every subsequent** iteration, `l0` is updated with: [2](#0-1) 

where `limbs_for_int` is defined as: [3](#0-2) 

`bits()` on a `num_bigint::BigInt` returns the number of bits in the **absolute value**, not accounting for the sign-extension byte. For a positive integer whose top bit is 1 (e.g., 254 = `0xfe`), CLVM's signed big-endian encoding requires a leading `0x00` byte, making the serialized length `ceil(bits/8) + 1`. `limbs_for_int` returns `ceil(bits/8)`, which is 1 byte short.

The test for `limbs_for_int` explicitly confirms this discrepancy — it strips leading zeros from the expected value, meaning it intentionally measures the internal representation size, not the CLVM wire size: [4](#0-3) 

`int_atom` returns the actual atom byte length from the allocator: [5](#0-4) 

The two functions measure different things. After the first iteration, `l0` is wrong by −1 whenever the intermediate product's top bit is set.

---

### Impact Explanation

The cost formula `(l0 + l1) * LINEAR + (l0 * l1) / SQUARE_DIVIDER` is supposed to model the computational cost of multiplying two numbers of byte sizes `l0` and `l1`. When `l0` is underestimated by 1, the charged cost is:

- Linear term: undercharged by `MUL_LINEAR_COST_PER_BYTE` = 6 per operation
- Quadratic term: undercharged by `l1 / MUL_SQUARE_COST_PER_BYTE_DIVIDER` = `l1 / 128` per operation

For a chain of N multiplications with large operands (e.g., l1 near 256 bytes), the quadratic undercharge is `256/128 = 2` per step, and the total undercharge grows with N. More critically, the intermediate size guard: [2](#0-1) 

also uses `limbs_for_int`, so an intermediate result of actual CLVM size 1025 bytes (top bit set, `limbs_for_int` = 1024) passes the guard. This allows larger-than-intended intermediate values, compounding the cost undercharge.

In a blockchain consensus context, this means an attacker-crafted CLVM program can perform more multiplication work than the declared cost limit permits, potentially causing nodes to accept programs that should be rejected, or causing consensus divergence between nodes with different cost-accounting implementations.

---

### Likelihood Explanation

The trigger is straightforward: any sequence of multiplications where intermediate products have their top bit set. A concrete example: start with `127` (1 byte, top bit 0), multiply by `2` repeatedly. Each result `254`, `508`, `1016`, … has its top bit set, so `limbs_for_int` underestimates by 1 at every step. An attacker submitting CLVM bytes encoding `(* 127 2 2 2 ...)` with many `2`s triggers the undercharge on every step. This is fully attacker-controlled via the CLVM program bytes passed to `run_program` / `run_serialized_chia_program`. [6](#0-5) 

---

### Recommendation

Replace `limbs_for_int` in the `l0` update with a function that computes the actual CLVM serialized byte length, accounting for the sign-extension byte. Specifically, after each multiplication, compute `l0` as the byte length that `a.new_number(total)` would produce (i.e., the length `a.atom_len` would return on the resulting node), rather than `v.bits().div_ceil(8)`. This makes the cost accounting consistent with the first-iteration measurement from `int_atom`.

---

### Proof of Concept

Craft a CLVM program `(* 0x7f 0x02 0x02 0x02 ...)` (127 × 2 × 2 × 2 × …):

- After step 1: total = 254 (`0xfe`). CLVM byte size = 2 (`[0x00, 0xfe]`). `limbs_for_int(254)` = `ceil(8/8)` = **1**. `l0` is set to 1 instead of 2.
- Step 2 cost uses `l0 = 1` instead of `l0 = 2`: undercharges by `(2−1)*6 + (2−1)*1/128` = **6 cost units**.
- After step 2: total = 508 (`0x01fc`). `limbs_for_int(508)` = `ceil(9/8)` = 2. CLVM size = 2. No discrepancy here.
- After step 3: total = 1016 (`0x03f8`). `limbs_for_int(1016)` = `ceil(10/8)` = 2. CLVM size = 2. No discrepancy.
- After step 4: total = 2032 (`0x07f0`). `limbs_for_int(2032)` = `ceil(11/8)` = 2. CLVM size = 2. No discrepancy.
- After step 7: total = 16256 (`0x3f80`). `limbs_for_int` = 2. CLVM size = 2.
- After step 8: total = 32512 (`0x7f00`). `limbs_for_int` = 2. CLVM size = 2.
- After step 9: total = 65024 (`0xfe00`). CLVM size = 3 (`[0x00, 0xfe, 0x00]`). `limbs_for_int(65024)` = `ceil(16/8)` = **2**. `l0` set to 2 instead of 3. Undercharge repeats.

The pattern recurs every time the product crosses a byte boundary with the top bit set, giving an attacker a reliable, repeatable undercharge vector across a long multiplication chain. [3](#0-2) [7](#0-6)

### Citations

**File:** src/more_ops.rs (L100-102)
```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
```

**File:** src/more_ops.rs (L109-116)
```rust
    // redundant leading zeros don't count, since they aren't stored internally
    let expected = if !bytes.is_empty() && bytes[0] == 0 {
        bytes.len() - 1
    } else {
        bytes.len()
    };
    assert_eq!(limbs_for_int(&bigint), expected);
}
```

**File:** src/more_ops.rs (L586-656)
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
        }

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
    }
    let total = a.new_number(total)?;
    Ok(malloc_cost(a, cost, total))
}
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

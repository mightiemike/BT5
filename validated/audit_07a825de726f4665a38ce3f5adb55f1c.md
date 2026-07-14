### Title
`limbs_for_int` Underestimates CLVM-Encoded Byte Length in `op_multiply` Cost Calculation, Enabling Undercharged Execution - (File: `src/more_ops.rs`)

---

### Summary

`op_multiply` tracks the running byte-size of the intermediate product using `limbs_for_int`, which computes `v.bits().div_ceil(8)` — the number of bytes needed for the *magnitude* of the number. However, CLVM uses two's-complement big-endian encoding, where any positive number whose high bit is set requires an extra leading `0x00` byte. This means `limbs_for_int` systematically underestimates the actual CLVM-encoded byte length by 1 for such values, causing the cost charged for each subsequent multiplication step to be lower than the actual computational work performed.

---

### Finding Description

In `op_multiply`, after each multiplication step, the running size of the accumulator is updated:

```rust
l0 = limbs_for_int(&total);
``` [1](#0-0) 

`limbs_for_int` is defined as:

```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
``` [2](#0-1) 

`BigInt::bits()` returns the number of bits in the **absolute value**, ignoring the sign bit. For a positive number like `128` (`0x80`), `bits()` = 8, so `limbs_for_int(128)` = 1. But CLVM's canonical two's-complement encoding of `128` is `0x00 0x80` — **2 bytes** — because the high bit of `0x80` would otherwise be interpreted as a negative sign. The allocator's `new_number` stores numbers in this canonical form, so `a.atom_len(ptr)` for `128` returns 2.

The test helper in the same file confirms this discrepancy is baked in:

```rust
// redundant leading zeros don't count, since they aren't stored internally
let expected = if !bytes.is_empty() && bytes[0] == 0 {
    bytes.len() - 1
} else {
    bytes.len()
};
assert_eq!(limbs_for_int(&bigint), expected);
``` [3](#0-2) 

This means `limbs_for_int` intentionally strips the leading zero — but the cost model must account for the actual encoded size, not the stripped magnitude size.

The underestimated `l0` is then used in the next iteration's cost formula:

```rust
cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
``` [4](#0-3) [5](#0-4) 

The same pattern appears in `op_ash` and `op_lsh`, where `l1 = limbs_for_int(&v)` is used for the output size in the cost formula, again underestimating by 1 for positive results with high bit set. [6](#0-5) [7](#0-6) 

The first argument's size `l0` is correctly initialized from `int_atom`, which returns `a.atom_len(args)` — the actual stored byte length:

```rust
(total, l0) = int_atom(a, arg, "*")?;
``` [8](#0-7) [9](#0-8) 

So the first argument is measured correctly, but every intermediate product is measured with the wrong metric.

---

### Impact Explanation

The cost model for `op_multiply` is supposed to reflect the actual computational cost of multiplying two big integers, which scales with their encoded byte lengths. When an intermediate product has its high bit set (e.g., `0x80`, `0x8000`, `0x800000`…), `limbs_for_int` returns a value 1 byte smaller than the actual CLVM encoding. The next multiplication step is therefore undercharged by:

- `1 * MUL_LINEAR_COST_PER_BYTE` = 6 cost units (linear term)
- `1 * l1 / MUL_SQUARE_COST_PER_BYTE_DIVIDER` additional units (quadratic term) [10](#0-9) 

For a chain of multiplications where every intermediate result has its high bit set, the undercharge accumulates across all steps. An attacker can craft a CLVM program that stays within the declared cost budget on one node implementation but exceeds the true computational cost, causing **consensus divergence**: nodes that compute cost correctly would reject the program while nodes using this code would accept it.

---

### Likelihood Explanation

The trigger is attacker-controlled CLVM bytes passed to `op_multiply` with 3 or more arguments. The attacker simply needs to choose operands such that intermediate products land on values with their high bit set (e.g., multiplying by 2 repeatedly starting from `0x40` produces `0x80`, `0x8000`, etc.). This is trivially constructible and requires no special privileges. The `op_multiply` operator is a standard, always-available CLVM opcode reachable from any program.

---

### Recommendation

Replace `limbs_for_int` with a function that computes the actual CLVM-canonical encoded byte length, accounting for the extra leading zero byte required for positive numbers with their high bit set:

```rust
fn encoded_len_for_int(v: &Number) -> usize {
    let bits = v.bits() as usize;
    if bits == 0 {
        return 0; // zero encodes as empty atom
    }
    let magnitude_bytes = bits.div_ceil(8);
    // positive numbers with high bit set need a leading 0x00
    if v.sign() == Sign::Plus && (bits % 8 == 0) {
        magnitude_bytes + 1
    } else {
        magnitude_bytes
    }
}
```

Use this in place of `limbs_for_int` at line 649 of `op_multiply`, and at the output-size calculations in `op_ash` (line 927) and `op_lsh` (line 996).

---

### Proof of Concept

Consider `(* 0x40 2 2)`:

1. First arg: `0x40` = 64. `int_atom` returns `l0 = 1` (1 byte, correct).
2. Second arg: `2`. `l1 = len_for_value(2)` = 1. `total = 128`. `l0 = limbs_for_int(128)` = **1** (wrong; CLVM encodes 128 as `0x00 0x80` = 2 bytes).
3. Third arg: `2`. `l1 = 1`. Cost uses `l0 = 1` instead of `l0 = 2`. Undercharge = `(2-1+1)*6 + (2-1)*1/128` ≈ 6 cost units.

For larger intermediate values (e.g., 256-byte products with high bit set), the undercharge from the quadratic term `(l0 * l1) / 128` becomes significant. An attacker can chain many such multiplications to accumulate a meaningful cost deficit, executing more computation than the budget permits. [11](#0-10)

### Citations

**File:** src/more_ops.rs (L34-37)
```rust
const MUL_BASE_COST: Cost = 92;
const MUL_COST_PER_OP: Cost = 885;
const MUL_LINEAR_COST_PER_BYTE: Cost = 6;
const MUL_SQUARE_COST_PER_BYTE_DIVIDER: Cost = 128;
```

**File:** src/more_ops.rs (L100-102)
```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
```

**File:** src/more_ops.rs (L109-115)
```rust
    // redundant leading zeros don't count, since they aren't stored internally
    let expected = if !bytes.is_empty() && bytes[0] == 0 {
        bytes.len() - 1
    } else {
        bytes.len()
    };
    assert_eq!(limbs_for_int(&bigint), expected);
```

**File:** src/more_ops.rs (L592-655)
```rust
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
```

**File:** src/more_ops.rs (L927-929)
```rust
    let l1 = limbs_for_int(&v);
    let r = a.new_number(v)?;
    let cost = ASHIFT_BASE_COST + ((l0 + l1) as Cost) * ASHIFT_COST_PER_BYTE;
```

**File:** src/more_ops.rs (L996-998)
```rust
    let l1 = limbs_for_int(&v);
    let r = a.new_number(v)?;
    let cost = LSHIFT_BASE_COST + ((l0 + l1) as Cost) * LSHIFT_COST_PER_BYTE;
```

**File:** src/op_utils.rs (L248-255)
```rust
pub fn int_atom(a: &Allocator, args: NodePtr, op_name: &str) -> Result<(Number, usize)> {
    match a.sexp(args) {
        SExp::Atom => Ok((a.number(args), a.atom_len(args))),
        _ => Err(EvalErr::InvalidOpArg(
            args,
            format!("Requires Int Argument: {op_name}"),
        ))?,
    }
```

### Title
`op_multiply` Undercharges Execution Cost via Inconsistent Size Metric for Intermediate Products — (`File: src/more_ops.rs`)

---

### Summary

`op_multiply` in `src/more_ops.rs` measures the byte-size of the first operand using `int_atom` (which returns the CLVM atom encoding length, including a mandatory leading zero byte for positive integers whose high bit is set), but after each multiplication it updates the running size variable `l0` using `limbs_for_int(&total)`, which returns only the unsigned magnitude byte count and omits the sign-padding byte. This inconsistency causes the cost of every subsequent multiplication step to be undercharged by up to one byte's worth of linear and quadratic cost terms whenever the intermediate product has its most-significant bit set.

---

### Finding Description

`limbs_for_int` is defined as:

```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
``` [1](#0-0) 

`v.bits()` returns the number of significant bits in the **unsigned magnitude**. For a positive integer whose highest bit is 1 (e.g., 255 = `0xff`), `bits()` = 8, so `limbs_for_int` returns 1. But the CLVM atom encoding of 255 is `[0x00, 0xff]` — two bytes — because a leading zero is required to signal a positive sign. The internal test confirms this:

```rust
fn limb_test_helper(bytes: &[u8]) {
    let expected = if !bytes.is_empty() && bytes[0] == 0 {
        bytes.len() - 1   // strips the sign byte
    } else {
        bytes.len()
    };
    assert_eq!(limbs_for_int(&bigint), expected);
}
``` [2](#0-1) 

In `op_multiply`, the first operand's size is obtained via `int_atom`, which returns `a.atom_len(args)` — the true CLVM atom byte length including any sign byte:

```rust
if first_iter {
    (total, l0) = int_atom(a, arg, "*")?;
    ...
}
``` [3](#0-2) 

After each multiplication, `l0` is updated with the inconsistent metric:

```rust
l0 = limbs_for_int(&total);
``` [4](#0-3) 

The cost formula for every subsequent operand is:

```rust
cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
``` [5](#0-4) 

Whenever `total` has its high bit set, `limbs_for_int` returns a value one less than `int_atom` would return for the same number. The cost for the next multiplication step is therefore computed with `l0` underestimated by 1, producing:

- **Undercharge per step** = `MUL_LINEAR_COST_PER_BYTE × 1 + (l1 / MUL_SQUARE_COST_PER_BYTE_DIVIDER)` = at least 6 cost units per affected step (with `MUL_LINEAR_COST_PER_BYTE = 6`).

The same pattern appears in `op_ash` and `op_lsh`, where `l1 = limbs_for_int(&v)` is used for the result size while `l0` was obtained from `int_atom`: [6](#0-5) [7](#0-6) 

---

### Impact Explanation

The cost model is the primary resource-exhaustion guard in CLVM. Undercharging `op_multiply` allows an attacker to submit programs whose true computational cost exceeds `max_cost` but whose measured cost does not. This has two concrete consequences:

1. **Consensus divergence**: Any alternative CLVM implementation that correctly measures intermediate product sizes using atom byte length (as `int_atom` does) will compute a higher cost and reject the program, while this implementation accepts it. This breaks the consensus invariant that all nodes agree on program validity.
2. **Resource exhaustion**: Programs that should be rejected as too expensive are accepted, allowing more computation per block than the cost limit intends.

---

### Likelihood Explanation

The trigger condition — an intermediate product with its high bit set — is trivially achievable. Multiplying any two positive integers whose product has its MSB set (e.g., `127 × 2 = 254 = 0xfe`) satisfies it. An attacker who can submit CLVM programs (e.g., via puzzle solutions in Chia transactions) can reliably trigger this path. The `op_multiply` operator is a standard, always-available opcode with no flag guard.

---

### Recommendation

Replace `limbs_for_int(&total)` with a measurement that matches `int_atom`'s atom-byte-length semantics. The simplest fix is to allocate the intermediate result and measure its atom length, or to add 1 to `limbs_for_int` when the result's high bit is set:

```rust
// After: total *= ...;
// Replace:
l0 = limbs_for_int(&total);
// With (accounting for sign byte):
l0 = {
    let bits = total.bits() as usize;
    let mag_bytes = bits.div_ceil(8);
    // if the MSB of the magnitude is set, CLVM encoding needs a leading zero
    if bits > 0 && bits % 8 == 0 { mag_bytes + 1 } else { mag_bytes }
};
```

Apply the same correction to `op_ash` and `op_lsh` where `l1 = limbs_for_int(&v)` is used for the result size.

---

### Proof of Concept

CLVM program (pseudocode): `(* (q . 127) (q . 2) (q . 2))`

- Step 1: `l0 = int_atom(127)` → atom `[0x7f]` → `l0 = 1` ✓
- Multiply: `total = 127 × 2 = 254`. Atom encoding: `[0x00, 0xfe]` (2 bytes, sign byte needed).
  - `limbs_for_int(254)` = `bits()=8`, `div_ceil(8,8)=1` → `l0 = 1` ✗ (should be 2)
- Step 2 cost uses `l0=1, l1=1`:
  - Charged: `(1+1)×6 + (1×1)/128 = 12`
  - Correct: `(2+1)×6 + (2×1)/128 = 18`
  - **Undercharge: 6 cost units**

With a chain of multiplications producing large numbers near the 1024-byte limit, the cumulative undercharge can reach hundreds of cost units per invocation, allowing programs that exceed `max_cost` to pass validation. [1](#0-0) [8](#0-7)

### Citations

**File:** src/more_ops.rs (L100-102)
```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
```

**File:** src/more_ops.rs (L104-116)
```rust
#[cfg(test)]
fn limb_test_helper(bytes: &[u8]) {
    let bigint = Number::from_signed_bytes_be(bytes);
    println!("{} bits: {}", &bigint, &bigint.bits());

    // redundant leading zeros don't count, since they aren't stored internally
    let expected = if !bytes.is_empty() && bytes[0] == 0 {
        bytes.len() - 1
    } else {
        bytes.len()
    };
    assert_eq!(limbs_for_int(&bigint), expected);
}
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

**File:** src/more_ops.rs (L927-930)
```rust
    let l1 = limbs_for_int(&v);
    let r = a.new_number(v)?;
    let cost = ASHIFT_BASE_COST + ((l0 + l1) as Cost) * ASHIFT_COST_PER_BYTE;
    Ok(malloc_cost(a, cost, r))
```

**File:** src/more_ops.rs (L996-999)
```rust
    let l1 = limbs_for_int(&v);
    let r = a.new_number(v)?;
    let cost = LSHIFT_BASE_COST + ((l0 + l1) as Cost) * LSHIFT_COST_PER_BYTE;
    Ok(malloc_cost(a, cost, r))
```

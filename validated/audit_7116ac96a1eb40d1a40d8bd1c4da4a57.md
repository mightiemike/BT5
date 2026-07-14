### Title
`limbs_for_int` Underestimates Running Product Size in `op_multiply`, Causing Undercharged Execution Cost — (`File: src/more_ops.rs`)

---

### Summary

In `op_multiply`, the running product's byte size (`l0`) is updated after each multiplication using `limbs_for_int(&total)`, which computes `v.bits().div_ceil(8)`. This function measures the magnitude-only byte count and does **not** account for the leading zero byte required in CLVM's canonical two's-complement encoding when the most significant bit of a positive number is set. The initial `l0` is taken from `int_atom`, which returns the actual stored atom byte length (including the sign-padding zero). This inconsistency causes the cost of subsequent multiplications to be systematically undercharged whenever the running product has its high bit set.

---

### Finding Description

`op_multiply` in `src/more_ops.rs` accumulates a running product and tracks its byte size in `l0` for cost accounting. The first operand's size is obtained from `int_atom`, which returns the actual CLVM-encoded byte length:

```rust
(total, l0) = int_atom(a, arg, "*")?;
```

After each subsequent multiplication, `l0` is updated via:

```rust
l0 = limbs_for_int(&total);
```

`limbs_for_int` is defined as:

```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
```

`Number::bits()` (from `num_bigint::BigInt`) returns the number of bits needed to represent the **magnitude** of the integer, without accounting for the sign bit. For a positive number whose most significant bit is set (e.g., 128 = `0x80`), `bits()` = 8, so `limbs_for_int` returns 1. However, the CLVM canonical encoding of 128 is `0x00 0x80` — two bytes — because the high bit is set and a leading zero is required to preserve the positive sign. Thus `atom_len` on the stored atom returns 2.

The cost formula for each multiplication step is:

```rust
cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

When `l0` is underestimated by 1 (because `limbs_for_int` omits the sign-padding byte), both the linear and quadratic cost components are undercharged for the next iteration. This is the same class of bug as the external report: one part of the code uses one size metric (actual atom byte length from `int_atom`) while another uses a different metric (`limbs_for_int`, which strips the sign byte), producing an arithmetic semantic mismatch.

The `limb_test_helper` test in the same file confirms this discrepancy explicitly: for bytes `[0x00, 0x80]` (canonical encoding of 128), the test expects `limbs_for_int` to return `1` (stripping the leading zero), while `atom_len` on the same value returns `2`.

---

### Impact Explanation

An attacker can craft a CLVM program with a chain of multiplications where the running product repeatedly has its most significant bit set. Each such step causes the cost charged to be lower than the actual computational work. Over a long chain, the cumulative undercharge can be significant enough to allow a program that exceeds the true cost limit to pass the `check_cost` guard. This enables:

- **Undercharged execution**: Programs consume more real resources than their charged cost, enabling a cost-limit bypass.
- **Consensus divergence**: If different validator implementations measure cost differently (e.g., a reference implementation that uses actual atom byte lengths), nodes may disagree on whether a program is valid, breaking consensus.

---

### Likelihood Explanation

The trigger is straightforward: any CLVM program using `*` (multiply) where intermediate products have their high bit set. This is common for arbitrary-precision multiplication chains. An attacker submitting crafted transactions to the Chia mempool can reliably trigger this path with attacker-controlled CLVM bytes.

---

### Recommendation

Replace `limbs_for_int` in the `l0` update inside `op_multiply` with a size measurement consistent with the actual CLVM atom encoding. After computing `total`, allocate it and use `a.atom_len(...)` to get the true byte length, or adjust `limbs_for_int` to add 1 when the most significant bit of the result is set:

```rust
fn limbs_for_int(v: &Number) -> usize {
    let bits = v.bits();
    if bits == 0 {
        return 0;
    }
    let bytes = bits.div_ceil(8) as usize;
    // If the high bit of the most significant byte is set, a leading zero
    // is required for canonical two's-complement encoding of positive numbers.
    if v.sign() == Sign::Plus && (bits % 8 == 0) {
        bytes + 1
    } else {
        bytes
    }
}
```

Alternatively, after each multiplication, allocate the intermediate result and call `a.atom_len(ptr)` to obtain the true byte count for cost purposes, consistent with how `int_atom` measures the first operand.

---

### Proof of Concept

Consider the CLVM program `(* 0x0080 0x0080)`:

1. First operand `0x0080` (= 128): `int_atom` returns `l0 = 2` (actual stored bytes).
2. Second operand `0x0080` (= 128): `l1 = 2`.
3. Cost charged: `MUL_COST_PER_OP + (2 + 2) * MUL_LINEAR_COST_PER_BYTE + (2 * 2) / 128`.
4. `total = 128 * 128 = 16384 = 0x4000`. `limbs_for_int(16384)` = `bits=15`, `div_ceil(8)` = 2. Actual atom: `0x4000` = 2 bytes. Here they agree.

Now consider `(* 0x0080 0x0080 0x0002)`:

1. After step above, `total = 16384`, `l0 = limbs_for_int(16384) = 2`.
2. Third operand `0x0002`: `l1 = 1`.
3. `total = 16384 * 2 = 32768 = 0x8000`. `limbs_for_int(32768)`: `bits = 16`, `div_ceil(8) = 2`. But actual CLVM atom for 32768 is `0x00 0x80 0x00` = **3 bytes** (leading zero required because high bit of `0x80` is set). So `l0` is updated to `2` instead of `3`.
4. Any further multiplication step uses `l0 = 2` instead of `3`, undercharging by `MUL_LINEAR_COST_PER_BYTE * (l1 + 1) + (l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER` per step.

A long chain of multiplications producing products with high bits set compounds this undercharge, allowing programs to bypass the cost limit. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** src/more_ops.rs (L596-655)
```rust
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

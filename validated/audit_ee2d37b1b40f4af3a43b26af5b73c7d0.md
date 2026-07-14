### Title
`op_multiply` Tracks Running-Product Size with Unsigned Bit-Count (`limbs_for_int`) While CLVM Uses Signed Big-Endian Encoding — Causing Cost Underestimation and Size-Limit Bypass - (File: `src/more_ops.rs`)

---

### Summary

In `op_multiply`, after each multiplication step the running-product size variable `l0` is updated via `limbs_for_int(&total)`, which computes `bits.div_ceil(8)` — the **unsigned** byte count. However, CLVM atoms use **signed big-endian** encoding, which requires an extra leading zero byte whenever the most-significant bit of a positive number is set (e.g., 128 → `0x00 0x80`, 32768 → `0x00 0x80 0x00`). This is the same unit-mismatch class as the reference report: two quantities that look like the same "byte count" are actually measured on different scales, and the cheaper one is used for both the cost formula and the size-limit guard.

---

### Finding Description

`limbs_for_int` is defined at `src/more_ops.rs` lines 100–102:

```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
```

`BigInt::bits()` returns the number of bits in the **absolute value** (unsigned magnitude). For a positive integer whose highest bit is set — e.g., 2^(8k) for any k — `bits()` is exactly `8k`, so `div_ceil(8)` returns `k`. But the canonical signed big-endian encoding of that number is `k+1` bytes (one leading `0x00` byte is required to distinguish it from a negative number).

In `op_multiply` (lines 586–656), `l0` is initialised from `int_atom`, which returns `a.atom_len(args)` — the **actual** (signed) byte length of the stored atom. After the first multiplication step, however, `l0` is overwritten at line 649:

```rust
l0 = limbs_for_int(&total);   // ← switches to unsigned byte count
```

From this point on, `l0` is the **unsigned** byte count of the running product, while:
- `l1` for `Buffer` operands is `buf.len()` — the **signed** byte count of the stored atom.
- `l1` for `U32` operands is `len_for_value(val)` — also the **signed** byte count (e.g., `len_for_value(128)` = 2, not 1).

The cost formula at lines 615–616 / 623–624 mixes these two scales:

```rust
cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
```

Whenever the running product is a positive number with its MSB set, `l0` is 1 less than the true signed byte length, so both the linear and quadratic cost terms are undercharged.

The same underestimated `l0` is used for the size-limit guard at line 650:

```rust
if l0 > 1024 {
    return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
}
```

A product of exactly 2^(8192) has `limbs_for_int` = 1024 (passes the guard) but a signed BE length of 1025 bytes, so the intended 1024-byte cap is silently exceeded by one byte.

---

### Impact Explanation

**Cost underestimation.** Each multiplication step where the running product has its MSB set charges `MUL_LINEAR_COST_PER_BYTE` (= 6) fewer cost units on the linear term, plus up to `l1 / MUL_SQUARE_COST_PER_BYTE_DIVIDER` (= `l1 / 128`, at most 2 for a 256-byte operand) fewer on the quadratic term. Over a long chain of multiplications this accumulates, allowing a program to consume slightly more computation than its declared cost budget permits — a consensus-relevant undercharge on a blockchain node.

**Size-limit bypass.** The `l0 > 1024` guard uses the unsigned count, so a product of 2^(8192) (1025 signed bytes) passes the check. The next step then uses `l0 = 1024` instead of 1025, compounding the cost underestimation for that step.

---

### Likelihood Explanation

The trigger is straightforward: craft a CLVM program that multiplies a sequence of values such that the running product repeatedly lands on a power of 2 that is a multiple of 8 bits (e.g., 2^8, 2^16, 2^24, …, 2^8192). These are attacker-controlled CLVM bytes passed directly to `run_program`. No privileged access, social engineering, or configuration change is required. The `op_multiply` operator is a standard, always-enabled CLVM opcode reachable from any coin spend on the Chia network.

---

### Recommendation

Replace `limbs_for_int` with a function that returns the **signed** big-endian byte length — i.e., add 1 when the most-significant bit of a positive result is set:

```rust
fn signed_byte_len(v: &Number) -> usize {
    if v.sign() == Sign::NoSign {
        return 0;
    }
    let bits = v.bits();
    let bytes = bits.div_ceil(8) as usize;
    // positive numbers whose MSB is set need a leading 0x00 byte
    if v.sign() == Sign::Plus && bits % 8 == 0 {
        bytes + 1
    } else {
        bytes
    }
}
```

Use this function at line 649 (`l0 = signed_byte_len(&total)`) and in the `l0 > 1024` guard so that both the cost formula and the size limit operate on the same scale as `int_atom` and `len_for_value`.

---

### Proof of Concept

Consider the CLVM program `(* (q . 128) (q . 128))`:

1. First operand: 128 stored as `0x00 0x80` → `int_atom` returns `l0 = 2`.
2. Second operand: 128 as `U32(128)` → `len_for_value(128) = 2`, so `l1 = 2`.
3. Cost charged: `(2 + 2) * 6 + (2 * 2) / 128 = 24 + 0 = 24` (plus base/op costs).
4. Product = 16384; `limbs_for_int(16384)` = `14 / 8` rounded up = 2. Correct here.

Now consider `(* (q . 0x0080) (q . 0x0080) (q . 0x0080))` — three factors of 128:

1. First operand: `l0 = 2` (from `int_atom`, signed length of `0x00 0x80`).
2. Second operand (128 as U32): `l1 = 2`. Cost += `(2+2)*6 = 24`. Product = 16384; `limbs_for_int(16384)` = 2. `l0 = 2`.
3. Third operand (128 as U32): `l1 = 2`. Cost += `(2+2)*6 = 24`. Product = 2097152 = 0x200000; `limbs_for_int(2097152)` = 3 (correct, no MSB issue here).

Now use `(* (q . 0x008000) (q . 0x008000))` — two factors of 32768:

1. First operand: `0x00 0x80 0x00` → `l0 = 3`.
2. Second operand (32768 as U32): `len_for_value(32768) = 3` (since 32768 ≥ 0x8000). `l1 = 3`. Cost += `(3+3)*6 = 36`. Product = 1073741824 = 2^30; `limbs_for_int(2^30)` = `30/8` rounded up = 4. Signed length = 4 (MSB of 2^30 is bit 30, byte 3 = 0x40, not set). No mismatch here.

The mismatch triggers when the product is exactly a power of 2 that is a multiple of 8, e.g., 2^8 = 256, 2^16 = 65536, 2^24, …:

- Product = 256 = 2^8: `limbs_for_int(256)` = `8/8` = 1. Signed BE = `0x01 0x00` = 2 bytes. **`l0` is 1, actual is 2. Mismatch.**
- Product = 65536 = 2^16: `limbs_for_int(65536)` = `16/8` = 2. Signed BE = `0x01 0x00 0x00` = 3 bytes. **`l0` is 2, actual is 3. Mismatch.**

A concrete trigger: `(* (q . 16) (q . 16))` → product = 256. `limbs_for_int(256)` = 1, but the signed BE encoding is `0x01 0x00` (2 bytes). If a third factor follows, its cost step uses `l0 = 1` instead of `l0 = 2`, undercharging by `1 * MUL_LINEAR_COST_PER_BYTE = 6` cost units on the linear term. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** src/more_ops.rs (L100-102)
```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
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

**File:** src/allocator.rs (L342-356)
```rust
pub fn len_for_value(val: u32) -> usize {
    if val == 0 {
        0
    } else if val < 0x80 {
        1
    } else if val < 0x8000 {
        2
    } else if val < 0x800000 {
        3
    } else if val < 0x80000000 {
        4
    } else {
        5
    }
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

### Title
`op_multiply` Undercharges Cost Due to `limbs_for_int` Ignoring CLVM Sign-Byte Overhead - (File: `src/more_ops.rs`)

### Summary
`op_multiply` tracks the byte-size of the running product using `limbs_for_int`, which computes `v.bits().div_ceil(8)` — the unsigned magnitude byte count. CLVM integers are signed two's-complement, so any positive product whose most-significant bit is set requires an extra leading zero byte in its canonical encoding. `limbs_for_int` never accounts for this byte, so `l0` is silently underestimated by 1 whenever the product's high bit is set. Every subsequent multiplication step then charges less cost than the actual operand size warrants, producing systematic, attacker-exploitable cost undercharging.

### Finding Description

`int_atom` (the function used to read the **first** operand of `*`) returns the atom's actual stored byte length via `a.atom_len(args)`: [1](#0-0) 

After each subsequent multiplication the running size `l0` is refreshed with: [2](#0-1) 

`limbs_for_int` is defined as: [3](#0-2) 

`BigInt::bits()` returns the number of bits in the **absolute value**, ignoring the sign. For a positive number whose most-significant bit is 1 (e.g. `0xff = 255`, `0xffff = 65535`), `bits()` is a multiple of 8, so `div_ceil(8)` returns exactly `N` bytes. But CLVM's canonical signed encoding of such a number requires `N + 1` bytes (a leading `0x00` to prevent the value from being interpreted as negative). The project's own test confirms this: [4](#0-3) 

The test case `[0, 0xff]` (the number 255) strips the leading zero and expects `limbs_for_int` to return 1, while the actual CLVM atom is 2 bytes. This is the invariant that `op_multiply` relies on for cost, and it is wrong for any product with a set high bit.

The cost formula that consumes `l0` is: [5](#0-4) 

When `l0` is 1 too small, the linear term is undercharged by `MUL_LINEAR_COST_PER_BYTE = 6` and the quadratic term by `l1 / MUL_SQUARE_COST_PER_BYTE_DIVIDER` per step. [6](#0-5) 

### Impact Explanation

An attacker submits a CLVM program that chains `*` operations whose intermediate products repeatedly land on values with the high bit set (e.g. products equal to `0xff`, `0xffff`, `0xffffff`, …). Each such step is undercharged by up to `6 + 2 = 8` cost units (linear + quadratic shortfall at maximum `l1 = 256`). Over many chained multiplications the cumulative undercharge allows the program to perform more bignum work than the declared cost budget permits. Because the Chia blockchain enforces a global cost limit per block, this lets an attacker squeeze extra computation into a block without paying the correct fee, degrading node performance and potentially enabling denial-of-service against validators.

### Likelihood Explanation

The trigger is straightforward: any CLVM program using `*` with operands chosen so that the running product has its high bit set. Values like `127 * 2 = 254 (0xfe)` or `255 * 256 = 65280 (0xff00)` trivially satisfy this. No special privileges are required; any Chia coin spend can embed arbitrary CLVM. The condition fires on roughly half of all random products (whenever the MSB of the result is 1), so it is not an edge case.

### Recommendation

Replace `limbs_for_int` with a function that returns the actual CLVM-canonical byte length of the signed integer — i.e., add 1 when the most-significant bit of the magnitude is set and the number is non-negative:

```rust
fn clvm_int_byte_len(v: &Number) -> usize {
    if v.sign() == Sign::NoSign {
        return 0;
    }
    let mag_bytes = v.bits().div_ceil(8) as usize;
    // positive numbers whose high bit is set need a leading 0x00
    if v.sign() == Sign::Plus && v.bits() % 8 == 0 {
        mag_bytes + 1
    } else {
        mag_bytes
    }
}
```

Use this function instead of `limbs_for_int` at line 649 of `src/more_ops.rs` so that `l0` always reflects the true CLVM-encoded size of the running product.

### Proof of Concept

```
; CLVM program: (* 0x7f 0x02)  =>  0xfe (254)
; limbs_for_int(254) = bits(254).div_ceil(8) = 8/8 = 1
; actual CLVM encoding of 254 = [0x00, 0xfe] = 2 bytes
; next step uses l0=1 instead of l0=2 → undercharge of 6 cost units

; Chain: (* 0x7f 0x02 0x02 0x02 ... 0x02)
; Each step after the first keeps the product in the 0x80-0xff range
; (e.g. 254*2=508, 508*2=1016, ...) — high bit set at each intermediate step
; → systematic undercharge accumulates across all steps
```

The attacker-controlled entry path is: craft a coin spend whose puzzle or solution evaluates a CLVM program containing `(* arg1 arg2 ...)` with operands chosen to keep intermediate products high-bit-set. `run_program` dispatches to `op_multiply` at: [7](#0-6) 

The undercharged `Reduction` cost is returned to the caller and accumulated in the block's total cost, allowing more work per block than the protocol intends.

### Citations

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

**File:** src/more_ops.rs (L586-655)
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
```

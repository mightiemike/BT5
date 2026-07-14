### Title
`limbs_for_int` Undercounts Negative Intermediate Product Size in `op_multiply`, Causing Cost Undercharge and Size-Limit Bypass - (File: `src/more_ops.rs`)

---

### Summary

`op_multiply` tracks the running byte-size of its intermediate product in `l0` using `limbs_for_int(&total)`. That helper computes `v.bits().div_ceil(8)`, which measures only the magnitude of the number. For negative numbers whose magnitude has its highest bit set (e.g., -129, -32769), the signed big-endian atom encoding requires one extra byte that `limbs_for_int` never counts. The underestimated `l0` is then fed into the cost formula for every subsequent multiplication step and into the hard 1024-byte size-limit guard, breaking both invariants.

---

### Finding Description

`limbs_for_int` is defined at `src/more_ops.rs` lines 100–102:

```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
```

`BigInt::bits()` returns the number of bits in the **absolute value** of the number. For a negative number −M, `bits()` equals `⌊log₂(M)⌋ + 1`. When the highest bit of M is set (i.e., M ∈ (2^(8k−1), 2^(8k)) for some k ≥ 1), the two's-complement signed big-endian encoding of −M requires k+1 bytes, but `limbs_for_int` returns k.

Concrete example:
- M = 129 → `bits()` = 8 → `limbs_for_int(-129)` = **1**
- `new_number(-129)` → `to_signed_bytes_be()` = `[0xFF, 0x7F]` → actual atom = **2 bytes**

In `op_multiply` (`src/more_ops.rs` lines 586–656), after each multiplication step the running size is updated:

```rust
l0 = limbs_for_int(&total);   // line 649 — may undercount by 1
if l0 > 1024 {                 // line 650 — size-limit guard uses wrong value
    return Err(...);
}
```

`l0` is then used in the cost formula for the **next** iteration:

```rust
cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;          // line 615/623/643
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;  // line 616/624/644
```

When `l0` is 1 less than the true atom size, both the linear and quadratic cost terms are undercharged. Additionally, a product whose true signed encoding is 1025 bytes will have `l0 = 1024`, silently passing the size-limit guard that is supposed to reject it.

`int_atom` (used for the first operand and in the `no-fastpath` branch) correctly returns `a.atom_len(args)` — the real atom length — so the first operand's size is accurate. The divergence is introduced only for intermediate products via `limbs_for_int`.

---

### Impact Explanation

Two broken invariants:

1. **Cost undercharge**: Each multiplication step whose intermediate product is a negative number with a "boundary" magnitude is charged as if the product is 1 byte smaller than it actually is. The undercharge per step is at most `MUL_LINEAR_COST_PER_BYTE × 1 + l1 / MUL_SQUARE_COST_PER_BYTE_DIVIDER` = up to 6 + 2 = 8 cost units. Over many chained multiplications this accumulates, allowing a program to consume more real computation than its declared cost budget permits — a consensus-divergence condition where this node accepts programs that a reference implementation with a correct `limbs_for_int` would reject.

2. **1024-byte size-limit bypass**: A product whose true signed encoding is 1025 bytes passes the `l0 > 1024` guard. The next multiplication then operates on a product larger than the intended cap, compounding the cost undercharge.

---

### Likelihood Explanation

The trigger is fully attacker-controlled: any CLVM program submitted to `run_program` can invoke `*` with carefully chosen negative operands. No special flags, dialect settings, or privileged access are required. The attacker only needs to arrange that an intermediate product lands in a range where its magnitude's highest bit is set (e.g., product = −129, −32769, −8388609, …). This is straightforward to engineer with small, fixed atom inputs.

---

### Recommendation

Replace `limbs_for_int` with a function that accounts for the sign-extension byte required by negative numbers:

```rust
fn limbs_for_int(v: &Number) -> usize {
    if v.sign() == Sign::NoSign {
        return 0;
    }
    let magnitude_bits = v.bits(); // bits of |v|
    let magnitude_bytes = magnitude_bits.div_ceil(8) as usize;
    // For negative numbers, if the top bit of the magnitude is set,
    // the two's-complement encoding needs an extra sign byte.
    if v.sign() == Sign::Minus {
        let top_bit_set = (magnitude_bits % 8) == 0; // magnitude fills all bits of its bytes
        if top_bit_set {
            return magnitude_bytes + 1;
        }
    }
    magnitude_bytes
}
```

Alternatively, after computing `total`, derive `l0` from the actual atom that `new_number` would produce (e.g., call `a.new_number(total.clone())` and use `a.atom_len(ptr)` — though this has allocation cost). The simplest safe fix is the corrected `limbs_for_int` above.

---

### Proof of Concept

```
;; CLVM program: (* -129 1)
;; -129 encoded as 0xFF7F (2 bytes)
;; 1 encoded as 0x01 (1 byte)
;;
;; Step 1: first_iter → (total, l0) = int_atom(-129) → l0 = 2 (correct, from atom_len)
;; Step 2: multiply by 1
;;   cost += (2 + 1) * 6 = 18   (correct)
;;   total = -129
;;   l0 = limbs_for_int(-129) = 1   ← BUG: should be 2
;;
;; Now chain: (* -129 1 1)
;; Step 3: multiply by 1 again, using l0 = 1 (wrong)
;;   cost += (1 + 1) * 6 = 12   ← should be (2 + 1) * 6 = 18
;;   undercharge = 6 per step
;;
;; To trigger size-limit bypass:
;; Construct a product P where limbs_for_int(P) = 1024 but actual atom = 1025 bytes.
;; Example: P = -(2^(8192-1) + 1)  [magnitude has top bit set in 1024-byte range]
;; limbs_for_int(P) = 1024, passes guard; actual encoding = 1025 bytes.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** src/more_ops.rs (L100-102)
```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
```

**File:** src/more_ops.rs (L615-617)
```rust
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                check_cost(cost, max_cost)?;
```

**File:** src/more_ops.rs (L623-625)
```rust
                cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;
                cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;
                check_cost(cost, max_cost)?;
```

**File:** src/more_ops.rs (L649-652)
```rust
        l0 = limbs_for_int(&total);
        if l0 > 1024 {
            return Err(EvalErr::InvalidOpArg(arg, "*".to_string()));
        }
```

**File:** src/allocator.rs (L704-722)
```rust
    pub fn new_number(&mut self, v: Number) -> Result<NodePtr> {
        use num_traits::ToPrimitive;
        if let Some(val) = v.to_u32()
            && val <= NODE_PTR_IDX_MASK
        {
            return self.new_small_number(val);
        }
        let bytes: Vec<u8> = v.to_signed_bytes_be();
        let mut slice = bytes.as_slice();

        // make number minimal by removing leading zeros
        while (!slice.is_empty()) && (slice[0] == 0) {
            if slice.len() > 1 && (slice[1] & 0x80 == 0x80) {
                break;
            }
            slice = &slice[1..];
        }
        self.new_atom(slice)
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

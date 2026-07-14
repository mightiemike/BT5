### Title
`op_multiply` Cost Undercharge via `limbs_for_int` vs. Actual CLVM Atom Size Mismatch in Running-Product Size Tracking — (File: `src/more_ops.rs`)

### Summary

In `op_multiply`, the running-product byte-size variable `l0` is updated after each multiplication step using `limbs_for_int(&total)`. This function computes the minimal *unsigned* byte count (`v.bits().div_ceil(8)`), but the actual CLVM atom encoding for positive numbers whose most-significant byte has its high bit set requires one extra leading zero byte (signed big-endian). The result is that `l0` is systematically underestimated by 1 for a large class of products, causing the cost charged for every subsequent multiplication step to be lower than the cost model intends.

### Finding Description

`limbs_for_int` is defined as:

```rust
fn limbs_for_int(v: &Number) -> usize {
    v.bits().div_ceil(8) as usize
}
```

`BigInt::bits()` returns the number of bits in the **absolute value**, with no sign bit. For a positive number whose most-significant byte has its high bit set (e.g. 255 = `0xff`, 65025 = `0xfe01`, 32768 = `0x8000`), `bits().div_ceil(8)` gives the unsigned byte count, which is **one less** than the actual CLVM atom length. The CLVM atom for 255 is `0x00 0xff` (2 bytes); `limbs_for_int(255) = 1`.

The own test suite confirms this:

```rust
// redundant leading zeros don't count, since they aren't stored internally
let expected = if !bytes.is_empty() && bytes[0] == 0 {
    bytes.len() - 1   // strips the sign byte
} else {
    bytes.len()
};
assert_eq!(limbs_for_int(&bigint), expected);
```

The comment calls the leading zero "redundant," but for positive numbers with the high bit set the leading zero is **not** redundant — it is the sign byte that distinguishes `0xff` (−1) from `0x00ff` (255).

In `op_multiply`, after the first operand is loaded via `int_atom` (which returns the raw atom length, correctly including the sign byte), every subsequent step updates `l0` with the underestimating value:

```rust
l0 = limbs_for_int(&total);   // line 649 — may be 1 less than actual atom size
```

The cost formula for the next step then uses this underestimated `l0`:

```rust
cost += (l0 as Cost + l1) * MUL_LINEAR_COST_PER_BYTE;          // line 615/623/643
cost += (l0 as Cost * l1) / MUL_SQUARE_COST_PER_BYTE_DIVIDER;  // line 616/624/644
```

This is an arithmetic semantic mismatch: the first operand's size is measured by raw atom length (from `int_atom`), but the running product's size is measured by `limbs_for_int` (minimal unsigned byte count). The two metrics diverge for exactly the class of values where the
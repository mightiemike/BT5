Based on my analysis of the code, here is my finding:

---

### Title
`op_substr` Accepts Non-Canonical Integer Indices in MEMPOOL_MODE Because `i32_atom` Has No `ClvmFlags` Parameter — (`src/op_utils.rs`, `src/more_ops.rs`)

### Summary

`i32_atom` (and the underlying `i32_from_u8`) has no `ClvmFlags` parameter and therefore can never enforce `CANONICAL_INTS`. `op_substr` in `more_ops.rs` uses `i32_atom` for its start/end index arguments. As a result, a non-canonical atom like `[0x00, 0x00, 0x01]` (encoding of integer `1` with leading zeros) is silently accepted as a valid index even when `ClvmFlags::CANONICAL_INTS` is set (i.e., MEMPOOL_MODE).

### Finding Description

`uint_atom` accepts a `flags: ClvmFlags` parameter and explicitly enforces canonical encoding when `CANONICAL_INTS` is set: [1](#0-0) [2](#0-1) 

The test suite confirms that `uint_atom` with `CANONICAL_INTS` rejects `[0x00, 0x00, 0x01]`: [3](#0-2) 

By contrast, `i32_atom` has **no** `flags` parameter at all: [4](#0-3) 

It delegates to `i32_from_u8`, which delegates to `u32_from_u8_impl`. That function accepts any buffer up to 4 bytes, including `[0x00, 0x00, 0x01]` → `Some(1)`, with no canonical check: [5](#0-4) 

`op_substr` in `more_ops.rs` imports and uses `i32_atom` for its index arguments: [6](#0-5) 

There is no path by which `op_substr` can pass `ClvmFlags` into `i32_atom`, because the function signature does not accept it. The canonical-int guard is structurally absent for this operator.

### Impact Explanation

In MEMPOOL_MODE (`ClvmFlags::CANONICAL_INTS` set), the invariant is that all integer arguments to operators must use canonical encoding (no leading zeros). `op_substr` violates this invariant for its start and end index arguments. A CLVM program using `(substr atom [0x00 0x00 0x01] [0x00 0x00 0x02])` will be accepted and executed successfully by clvm_rs in MEMPOOL_MODE, while a strict implementation enforcing canonical ints for all operator arguments would reject it. This creates a divergence between clvm_rs and any stricter validator, which is the exact class of bug that can cause mempool/consensus inconsistency.

### Likelihood Explanation

The call path is direct and attacker-controlled: serialize a CLVM program with non-canonical integer atoms as `substr` indices, submit it through the public API (`run_serialized_chia_program` with MEMPOOL_MODE flags), and it executes without error. No special privileges or configuration are required.

### Recommendation

Add a `flags: ClvmFlags` parameter to `i32_atom` (mirroring `uint_atom`) and insert a canonical-int check analogous to the one in `uint_atom` (lines 67–79 of `src/op_utils.rs`). Update all call sites in `more_ops.rs` (including `op_substr`) to pass the active `flags` value.

### Proof of Concept

```rust
// Pseudocode: run (substr "hello" [0x00 0x00 0x01] [0x00 0x00 0x02]) in MEMPOOL_MODE
// [0x00, 0x00, 0x01] is non-canonical encoding of 1
// Expected: clvm_rs accepts it and returns "e"
// Expected from strict impl: EvalErr (non-canonical integer argument)
let result = run_serialized_chia_program(
    &serialized_program,
    &serialized_args,
    cost_limit,
    ClvmFlags::MEMPOOL_MODE.bits(),
);
assert!(result.is_ok()); // clvm_rs accepts — strict impl would reject
``` [4](#0-3) [7](#0-6)

### Citations

**File:** src/op_utils.rs (L47-52)
```rust
pub fn uint_atom<const SIZE: usize>(
    a: &Allocator,
    args: NodePtr,
    op_name: &str,
    flags: ClvmFlags,
) -> Result<u64> {
```

**File:** src/op_utils.rs (L67-79)
```rust
            if flags.contains(ClvmFlags::CANONICAL_INTS) {
                // strip potential zero
                if buf[0] == 0 {
                    if buf.len() < 2 || (buf[1] & 0x80) == 0 {
                        return Err(EvalErr::InvalidOpArg(
                            args,
                            format!(
                                "{op_name} requires u{0} arg with no leading zeros",
                                SIZE * 8
                            ),
                        ));
                    }
                    buf = &buf[1..];
```

**File:** src/op_utils.rs (L120-135)
```rust
pub fn i32_atom(a: &Allocator, args: NodePtr, op_name: &str) -> Result<i32> {
    match a.node(args) {
        NodeVisitor::Buffer(buf) => match i32_from_u8(buf) {
            Some(v) => Ok(v),
            _ => Err(EvalErr::InvalidOpArg(
                args,
                format!("{op_name} requires int32 args (with no leading zeros)"),
            ))?,
        },
        NodeVisitor::U32(val) => Ok(val as i32),
        NodeVisitor::Pair(_, _) => Err(EvalErr::InvalidOpArg(
            args,
            format!("{op_name} requires int32 args (with no leading zeros)"),
        ))?,
    }
}
```

**File:** src/op_utils.rs (L137-162)
```rust
fn u32_from_u8_impl(buf: &[u8], signed: bool) -> Option<u32> {
    if buf.is_empty() {
        return Some(0);
    }

    // too many bytes for u32
    if buf.len() > 4 {
        return None;
    }

    let sign_extend = (buf[0] & 0x80) != 0;
    let mut ret: u32 = if signed && sign_extend { 0xffffffff } else { 0 };
    for b in buf {
        ret <<= 8;
        ret |= *b as u32;
    }
    Some(ret)
}

pub fn u32_from_u8(buf: &[u8]) -> Option<u32> {
    u32_from_u8_impl(buf, false)
}

pub fn i32_from_u8(buf: &[u8]) -> Option<i32> {
    u32_from_u8_impl(buf, true).map(|v| v as i32)
}
```

**File:** src/op_utils.rs (L496-513)
```rust
    // u32, 4 bytes
    #[rstest]
    #[case(&[0x00,0x7f,0xff,0xff], "test requires u32 arg with no leading zeros")]
    #[case(&[0x00, 0x00, 0x01], "test requires u32 arg with no leading zeros")]
    #[case(&[0xff,0xff,0xff,0xff], "test requires positive int arg")]
    #[case(&[0xff], "test requires positive int arg")]
    #[case(&[0x80], "test requires positive int arg")]
    #[case(&[0x80,0,0,0], "test requires positive int arg")]
    #[case(&[1, 0xff,0xff,0xff,0xff], "test requires u32 arg (with no leading zeros)")]
    fn test_uint_atom_4_non_canonical(#[case] buf: &[u8], #[case] expected: &str) {
        use crate::allocator::Allocator;
        let mut a = Allocator::new();
        let n = a.new_atom(buf).unwrap();
        assert_eq!(
            uint_atom::<4>(&a, n, "test", ClvmFlags::CANONICAL_INTS),
            Err(EvalErr::InvalidOpArg(n, expected.to_string()))
        );
    }
```

**File:** src/more_ops.rs (L15-18)
```rust
use crate::op_utils::{
    MALLOC_COST_PER_BYTE, atom, atom_len, get_args, get_varargs, i32_atom, int_atom,
    malachite_int_atom, mod_group_order, new_atom_and_cost, nilp, u32_from_u8,
};
```

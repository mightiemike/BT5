### Title
`i32_atom` Ignores `CANONICAL_INTS` Flag, Allowing Non-Minimal Shift Amounts Through Mempool — (`src/op_utils.rs`, `src/more_ops.rs`)

---

### Summary

`op_ash` (and `op_lsh`) retrieve the shift amount via `i32_atom`, which has no `ClvmFlags` parameter and therefore cannot enforce `CANONICAL_INTS`. A 4-byte non-minimal encoding of a small integer (e.g., `[0x00, 0x00, 0x00, 0x01]` for value `1`) is silently accepted even when `MEMPOOL_MODE` (which includes `CANONICAL_INTS`) is active.

---

### Finding Description

**Call chain:**

```
op_ash (more_ops.rs:918)
  └─ i32_atom(a, n1, "ash")          ← no flags parameter
       └─ i32_from_u8(buf)
            └─ u32_from_u8_impl(buf, true)
                 └─ accepts any buf.len() ≤ 4, no leading-zero check
```

`op_ash` discards the `_flags: ClvmFlags` argument entirely: [1](#0-0) 

`i32_atom` takes no `ClvmFlags` parameter and delegates directly to `i32_from_u8`: [2](#0-1) 

`u32_from_u8_impl` performs only a length check (`> 4 → None`) and no canonical-encoding check: [3](#0-2) 

**Contrast with `uint_atom`**, which correctly gates on `CANONICAL_INTS` and rejects leading zeros: [4](#0-3) 

`MEMPOOL_MODE` explicitly includes `CANONICAL_INTS`: [5](#0-4) 

The existing test suite confirms that a 5-byte shift atom (`0x0000000001`) is rejected (length > 4), and that a 32-byte shift atom is rejected, but a 4-byte non-minimal atom (`[0x00, 0x00, 0x00, 0x01]`) is **not** tested and **not** rejected: [6](#0-5) 

---

### Impact Explanation

The claimed impact of **mempool/consensus divergence** is **not accurate** as stated. Here is the precise picture:

| Mode | `CANONICAL_INTS` set? | Accepts `[0x00,0x00,0x00,0x01]` as shift? |
|---|---|---|
| Consensus | No | Yes (by design) |
| Mempool (current code) | Yes (but ignored by `i32_atom`) | Yes (bug) |
| Mempool (intended) | Yes | No — should error |

Because **both** consensus and mempool accept the non-canonical encoding in the current code, there is **no node-to-node divergence** today. The actual impact is:

1. **Mempool policy bypass**: `CANONICAL_INTS` is supposed to make the mempool stricter than consensus for integer arguments. For shift amounts specifically, that strictness is silently absent. Any transaction using `ash`/`lsh` with a non-minimal 2-, 3-, or 4-byte shift atom bypasses the canonical check.
2. **No consensus split**: Consensus does not set `CANONICAL_INTS`, so it accepts these transactions regardless. They are valid on-chain.
3. **Future risk**: If a future consensus hard-fork enforces canonical integers (including for shift amounts), nodes running the current code would diverge from nodes running the fixed code, because the current code would accept non-canonical shift amounts in consensus while fixed nodes would reject them.

---

### Likelihood Explanation

The bug is trivially reachable: any CLVM program that calls `ash` or `lsh` with a shift atom that has leading zero bytes (e.g., `[0x00, 0x01]` for value 1) exercises this path. The `MEMPOOL_MODE` constant is used in production mempool evaluation. The gap between `uint_atom` (which correctly checks `CANONICAL_INTS`) and `i32_atom` (which does not) is a straightforward oversight.

---

### Recommendation

Add a `flags: ClvmFlags` parameter to `i32_atom` and enforce the canonical check inside it, mirroring `uint_atom`:

```rust
pub fn i32_atom(a: &Allocator, args: NodePtr, op_name: &str, flags: ClvmFlags) -> Result<i32> {
    match a.node(args) {
        NodeVisitor::Buffer(buf) => {
            if flags.contains(ClvmFlags::CANONICAL_INTS) {
                // reject non-minimal encodings
                if buf.len() > 1 && buf[0] == 0 && (buf[1] & 0x80) == 0 {
                    return Err(EvalErr::InvalidOpArg(args, ...));
                }
                if buf.len() > 1 && buf[0] == 0xff && (buf[1] & 0x80) != 0 {
                    return Err(EvalErr::InvalidOpArg(args, ...));
                }
            }
            match i32_from_u8(buf) { ... }
        }
        ...
    }
}
```

Update `op_ash` and `op_lsh` to pass `flags` through instead of discarding it.

---

### Proof of Concept

```rust
#[test]
fn test_ash_non_canonical_shift_under_canonical_ints() {
    use crate::allocator::Allocator;
    use crate::chia_dialect::{ClvmFlags, MEMPOOL_MODE};
    use crate::more_ops::op_ash;
    use crate::cost::Cost;

    let mut a = Allocator::new();
    // shift atom [0x00, 0x00, 0x00, 0x01] = non-minimal encoding of 1
    let shift = a.new_atom(&[0x00, 0x00, 0x00, 0x01]).unwrap();
    let val   = a.new_atom(&[0xcc]).unwrap();
    let nil   = a.nil();
    let args  = a.new_pair(shift, nil).unwrap();
    let args  = a.new_pair(val, args).unwrap();

    // Under MEMPOOL_MODE (CANONICAL_INTS set), this SHOULD return Err(InvalidOpArg)
    // but currently returns Ok(...) — demonstrating the bypass.
    let result = op_ash(&mut a, args, 10_000_000 as Cost, MEMPOOL_MODE);
    assert!(result.is_err(), "expected rejection of non-canonical shift under CANONICAL_INTS");
}
```

This test **fails** (i.e., `op_ash` returns `Ok`) with the current code, confirming the bypass.

### Citations

**File:** src/more_ops.rs (L918-921)
```rust
pub fn op_ash(a: &mut Allocator, input: NodePtr, _max_cost: Cost, _flags: ClvmFlags) -> Response {
    let [n0, n1] = get_args::<2>(a, input, "ash")?;
    let (i0, l0) = int_atom(a, n0, "ash")?;
    let a1 = i32_atom(a, n1, "ash")?;
```

**File:** src/op_utils.rs (L67-86)
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
                }
            } else {
                // strip leading zeros
                while !buf.is_empty() && buf[0] == 0 {
                    buf = &buf[1..];
                }
            }
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

**File:** src/op_utils.rs (L137-154)
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
```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** op-tests/test-more-ops.txt (L265-269)
```text
ash 0x00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000007 0x0000000000000000000000000000000000000000000000000000000000000001 => FAIL

; parameter isn't allowed to be wider than 32 bits
ash 0xcc 0x0000000001 => FAIL
ash 0xcc "foo" => FAIL
```

### Title
`CANONICAL_INTS` Flag Defined and Included in `MEMPOOL_MODE` but Never Enforced for Arithmetic Operators — (`File: src/op_utils.rs`, `src/more_ops.rs`)

---

### Summary

`ClvmFlags::CANONICAL_INTS` is declared, documented, and included in the production `MEMPOOL_MODE` constant, but the helper functions used by every arithmetic operator (`int_atom`, `i32_atom`, `malachite_int_atom`) never receive or consult the flag. The only place the flag is actually checked is `uint_atom()`, which is called exclusively for softfork cost/extension arguments inside `run_program.rs`. All arithmetic operators (`op_add`, `op_subtract`, `op_multiply`, `op_div`, `op_divmod`, `op_gr`, `op_ash`, `op_lsh`, `op_logand`, `op_logior`, `op_logxor`, `op_lognot`, `op_mod`, `op_modpow`) silently accept non-canonical integers even when the caller passes `MEMPOOL_MODE`.

---

### Finding Description

**Flag definition and intent.**
`CANONICAL_INTS` is defined with the explicit comment "require integers passed to operators use canonical representation, meaning no unnecessary leading zeros": [1](#0-0) 

It is unconditionally included in the production `MEMPOOL_MODE` constant: [2](#0-1) 

**Where the flag IS checked.**
`uint_atom()` in `src/op_utils.rs` accepts a `ClvmFlags` parameter and enforces the canonical-integer rule: [3](#0-2) 

`uint_atom()` is called only for the softfork cost and extension arguments inside `run_program.rs`: [4](#0-3) 

**Where the flag is NOT checked.**
`int_atom()`, `i32_atom()`, and `malachite_int_atom()` — the helpers used by every arithmetic operator — take no `flags` parameter and perform no canonical-integer check: [5](#0-4) [6](#0-5) [7](#0-6) 

`more_ops.rs` imports `int_atom`, `i32_atom`, and `malachite_int_atom` but never imports `uint_atom`: [8](#0-7) 

A grep for `CANONICAL_INTS` returns zero hits in `src/more_ops.rs`, confirming the flag is completely absent from all arithmetic operator implementations.

---

### Impact Explanation

When a caller invokes `run_serialized_chia_program` (or the Rust `run_program`) with `MEMPOOL_MODE` (which includes `CANONICAL_INTS`), every arithmetic operator — `+`, `-`, `*`, `/`, `divmod`, `>`, `ash`, `lsh`, `logand`, `logior`, `logxor`, `lognot`, `mod`, `modpow` — will silently accept atoms with unnecessary leading zero bytes. The mempool-mode policy that is supposed to reject such inputs is completely inoperative for these operators. The `CANONICAL_INTS` bit in `MEMPOOL_MODE` is therefore meaningless for the majority of operators it was intended to govern, directly mirroring the external report's pattern of a guard that is toggled but never consulted. [9](#0-8) 

---

### Likelihood Explanation

Any caller that passes `MEMPOOL_MODE` (or sets `CANONICAL_INTS` explicitly) and expects non-canonical integers to be rejected in arithmetic operators will be silently bypassed. An attacker submitting CLVM programs to a mempool can include atoms like `0x0001` (non-canonical encoding of `1`) as arguments to `+`, `-`, `*`, etc., and the mempool will accept them without error. The entry path is the standard `run_serialized_chia_program` Python API or the Rust `run_program` function — both are fully attacker-reachable via submitted transaction programs.

---

### Recommendation

Pass `ClvmFlags` through to `int_atom`, `i32_atom`, and `malachite_int_atom`, and add a canonical-integer check analogous to the one already present in `uint_atom`. Alternatively, add a dedicated `check_canonical_int(a, node, flags)` helper and call it at the top of each arithmetic operator that currently calls `int_atom` or `i32_atom`. The check should reject any atom whose first byte is `0x00` unless the second byte has its high bit set (i.e., the leading zero is required to preserve sign).

---

### Proof of Concept

```rust
use clvmr::allocator::Allocator;
use clvmr::chia_dialect::{ChiaDialect, MEMPOOL_MODE};
use clvmr::run_program::run_program;
use clvmr::serde::node_from_bytes;

fn main() {
    // Program: (+ (q . 0x0001) (q . 0x0002))
    // 0x0001 is a non-canonical encoding of 1 (has an unnecessary leading zero).
    // With CANONICAL_INTS set, this should be rejected by the arithmetic operator.
    let program_bytes: &[u8] = &[
        0xff, 0x10,             // (+ ...
        0xff, 0x01, 0x82, 0x00, 0x01,  // (q . 0x0001)  -- non-canonical
        0xff, 0x01, 0x01, 0x02, 0x80,  // (q . 0x0002)
    ];
    let mut allocator = Allocator::new();
    let program = node_from_bytes(&mut allocator, program_bytes).unwrap();
    let args = allocator.nil();
    let dialect = ChiaDialect::new(MEMPOOL_MODE);

    // Expected: Err (CANONICAL_INTS should reject 0x0001 as non-canonical)
    // Actual:   Ok  (int_atom() never checks the flag; 0x0001 is silently accepted)
    let result = run_program(&mut allocator, &dialect, program, args, u64::MAX);
    println!("{:?}", result); // prints Ok(...) — the flag has no effect
}
```

The `CANONICAL_INTS` flag is checked only inside `uint_atom` (softfork arguments). Because `op_add` and every other arithmetic operator call `int_atom` — which has no `flags` parameter — the non-canonical atom `0x0001` passes through unchallenged, demonstrating that the flag is wired into `MEMPOOL_MODE` but never consulted by the operators it was meant to govern.

### Citations

**File:** src/chia_dialect.rs (L27-30)
```rust
        /// require integers passed to operators use canonical representation,
        /// meaning no unnecessary leading zeros
        const CANONICAL_INTS = 0x0001;

```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/op_utils.rs (L47-86)
```rust
pub fn uint_atom<const SIZE: usize>(
    a: &Allocator,
    args: NodePtr,
    op_name: &str,
    flags: ClvmFlags,
) -> Result<u64> {
    match a.node(args) {
        NodeVisitor::Buffer(bytes) => {
            if bytes.is_empty() {
                return Ok(0);
            }

            if (bytes[0] & 0x80) != 0 {
                return Err(EvalErr::InvalidOpArg(
                    args,
                    format!("{op_name} requires positive int arg"),
                ))?;
            }

            let mut buf: &[u8] = bytes;
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

**File:** src/op_utils.rs (L258-271)
```rust
pub fn malachite_int_atom(
    a: &Allocator,
    args: NodePtr,
    op_name: &str,
) -> Result<(Malachite, usize)> {
    match a.node(args) {
        NodeVisitor::Buffer(buf) => Ok((malachite_number_from_u8(buf), buf.len())),
        NodeVisitor::U32(val) => Ok((val.into(), len_for_value(val))),
        NodeVisitor::Pair(_, _) => Err(EvalErr::InvalidOpArg(
            args,
            format!("Requires Int Argument: {op_name}"),
        ))?,
    }
}
```

**File:** src/run_program.rs (L385-392)
```rust
            let expected_cost = uint_atom::<8>(
                self.allocator,
                first(self.allocator, operand_list)?,
                "softfork",
                self.dialect.flags(),
            )?;
            if expected_cost > max_cost {
                return Err(EvalErr::CostExceeded);
```

**File:** src/more_ops.rs (L15-18)
```rust
use crate::op_utils::{
    MALLOC_COST_PER_BYTE, atom, atom_len, get_args, get_varargs, i32_atom, int_atom,
    malachite_int_atom, mod_group_order, new_atom_and_cost, nilp, u32_from_u8,
};
```

**File:** wheel/src/api.rs (L40-62)
```rust
pub fn run_serialized_chia_program(
    py: Python,
    program: &[u8],
    args: &[u8],
    max_cost: Cost,
    flags: u32,
) -> PyResult<(u64, LazyNode)> {
    let flags = ClvmFlags::from_bits_truncate(flags);
    let mut allocator = if flags.contains(ClvmFlags::LIMIT_HEAP) {
        Allocator::new_limited(500000000)
    } else {
        Allocator::new()
    };

    let r: Response = (|| -> PyResult<Response> {
        let program = node_from_bytes(&mut allocator, program).map_err(eval_to_py)?;
        let args = node_from_bytes(&mut allocator, args).map_err(eval_to_py)?;
        let dialect = ChiaDialect::new(flags);

        Ok(py.detach(|| run_program(&mut allocator, &dialect, program, args, max_cost)))
    })()?;
    adapt_response(py, allocator, r)
}
```

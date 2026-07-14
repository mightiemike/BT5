### Title
`CANONICAL_INTS` Flag Defined and Enforced for Softfork Arguments but Not for Arithmetic Operator Integer Inputs — (File: `src/op_utils.rs`, `src/chia_dialect.rs`)

---

### Summary

`ClvmFlags::CANONICAL_INTS` is defined with the explicit purpose of requiring canonical integer representation (no unnecessary leading zeros) for integers passed to operators. It is included in `MEMPOOL_MODE` and is enforced in `uint_atom()` for softfork cost and extension arguments. However, `int_atom()` — the function used by every arithmetic operator — accepts no `flags` parameter and performs no canonical-integer check. As a result, all arithmetic operators silently accept non-canonical integers even when `CANONICAL_INTS` is active, rendering the flag's stated invariant broken for the most common operator class.

---

### Finding Description

**The flag exists and is documented:**

`ClvmFlags::CANONICAL_INTS` (bit `0x0001`) is declared in `src/chia_dialect.rs` with the description *"require integers passed to operators use canonical representation, meaning no unnecessary leading zeros."* [1](#0-0) 

It is unconditionally included in `MEMPOOL_MODE`: [2](#0-1) 

**The flag is enforced only for softfork arguments:**

`uint_atom()` in `src/op_utils.rs` accepts `flags: ClvmFlags` and explicitly checks `CANONICAL_INTS`, rejecting atoms with unnecessary leading zeros: [3](#0-2) 

`uint_atom()` is called in `apply_op()` for the softfork expected-cost argument and in `parse_softfork_arguments()` for the extension argument: [4](#0-3) [5](#0-4) 

**The flag is absent from `int_atom()` — used by every arithmetic operator:**

`int_atom()` takes no `flags` parameter and performs no canonical-integer check: [6](#0-5) 

Every arithmetic operator dispatched by `ChiaDialect::op()` — `op_add`, `op_subtract`, `op_multiply`, `op_div`, `op_divmod`, `op_mod`, `op_gr`, `op_ash`, `op_lsh`, `op_logand`, `op_logior`, `op_logxor`, `op_lognot`, `op_modpow` — calls `int_atom()` for its operands: [7](#0-6) [8](#0-7) [9](#0-8) 

None of these operators receive or propagate `flags` to `int_atom()`. The `_flags: ClvmFlags` parameter is accepted but ignored in the slow-path arithmetic: [10](#0-9) 

---

### Impact Explanation

In `MEMPOOL_MODE`, the Chia mempool validator is supposed to enforce stricter rules than consensus mode. `CANONICAL_INTS` is one such rule: it is intended to reject programs that pass integers with unnecessary leading zeros to operators. Because `int_atom()` never checks this flag, any CLVM program that passes a non-canonical integer (e.g., `0x0001` instead of `0x01`, or `0x000000ff` instead of `0x00ff`) to an arithmetic operator will pass mempool validation without error. The mempool's stated invariant — that all integer arguments to operators are canonical — is silently violated for the entire arithmetic operator class. This weakens the mempool's strictness guarantee and allows non-canonical transaction encodings to enter the mempool, which could be exploited to bypass mempool-level filters or policies that assume canonical encoding.

---

### Likelihood Explanation

Exploitation requires only crafting a CLVM program with a non-canonical integer literal as an argument to any arithmetic operator (e.g., `(+ (q . 0x0001) (q . 0x0002))`). No special privileges, keys, or access are required. The attacker-controlled entry path is the standard CLVM program bytes submitted to the mempool. The trigger is deterministic and reproducible.

---

### Recommendation

`int_atom()` should be extended to accept a `ClvmFlags` parameter and enforce the `CANONICAL_INTS` check when the flag is set, analogous to how `uint_atom()` already does. All arithmetic operator functions that currently accept `_flags: ClvmFlags` but ignore it should pass the flags through to `int_atom()`. Alternatively, a separate `canonical_int_atom()` wrapper can be introduced that calls `int_atom()` and then validates canonical form when `CANONICAL_INTS` is set.

---

### Proof of Concept

```
// MEMPOOL_MODE includes CANONICAL_INTS (0x0001)
// uint_atom() enforces it for softfork args — correct
// int_atom() does NOT enforce it — missing guard

// Attacker submits this CLVM in mempool mode:
// (+ (q . 0x000001) (q . 0x000002))
//
// 0x000001 is a non-canonical encoding of integer 1 (canonical: 0x01)
// 0x000002 is a non-canonical encoding of integer 2 (canonical: 0x02)
//
// Expected (per CANONICAL_INTS): EvalErr::InvalidOpArg — rejected
// Actual: Ok(Reduction(cost, 3)) — accepted silently
//
// Root cause: op_add -> int_atom() -> no flags check
// int_atom() signature: fn int_atom(a: &Allocator, args: NodePtr, op_name: &str)
//                                                                  ^^^^^^^^^^^^^^
//                                                   no ClvmFlags parameter at all
```

### Citations

**File:** src/chia_dialect.rs (L28-30)
```rust
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

**File:** src/run_program.rs (L357-362)
```rust
        let extension = self.dialect.softfork_extension(uint_atom::<4>(
            self.allocator,
            extension,
            "softfork",
            self.dialect.flags(),
        )? as u32);
```

**File:** src/run_program.rs (L385-390)
```rust
            let expected_cost = uint_atom::<8>(
                self.allocator,
                first(self.allocator, operand_list)?,
                "softfork",
                self.dialect.flags(),
            )?;
```

**File:** src/more_ops.rs (L411-416)
```rust
pub fn op_add(
    a: &mut Allocator,
    mut input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
```

**File:** src/more_ops.rs (L658-668)
```rust
pub fn op_div(a: &mut Allocator, input: NodePtr, max_cost: Cost, flags: ClvmFlags) -> Response {
    if flags.contains(ClvmFlags::MALACHITE) {
        return op_div_malachite(a, input, max_cost, flags);
    }
    let [v0, v1] = get_args::<2>(a, input, "/")?;
    let (a0, a0_len) = int_atom(a, v0, "/")?;
    let (a1, a1_len) = int_atom(a, v1, "/")?;
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
```

**File:** src/more_ops.rs (L706-716)
```rust
pub fn op_divmod(a: &mut Allocator, input: NodePtr, max_cost: Cost, flags: ClvmFlags) -> Response {
    if flags.contains(ClvmFlags::MALACHITE) {
        return op_divmod_malachite(a, input, max_cost, flags);
    }
    let [v0, v1] = get_args::<2>(a, input, "divmod")?;
    let (a0, a0_len) = int_atom(a, v0, "divmod")?;
    let (a1, a1_len) = int_atom(a, v1, "divmod")?;
    if flags.contains(ClvmFlags::DISABLE_OP) && a0_len > 2048 {
        return Err(EvalErr::InvalidOpArg(input, "divmod".to_string()));
    }
    if a0_len > 256 || a1_len > 1024 {
```

**File:** src/more_ops.rs (L1250-1268)
```rust
pub fn op_modpow(a: &mut Allocator, input: NodePtr, max_cost: Cost, flags: ClvmFlags) -> Response {
    if flags.contains(ClvmFlags::MALACHITE) {
        return op_modpow_malachite(a, input, max_cost, flags);
    }
    let [base, exponent, modulus] = get_args::<3>(a, input, "modpow")?;

    let mut cost = MODPOW_BASE_COST;
    let (base, bsize) = int_atom(a, base, "modpow")?;
    cost += bsize as Cost * MODPOW_COST_PER_BYTE_BASE_VALUE;
    let (exponent, esize) = int_atom(a, exponent, "modpow")?;
    cost += (esize * esize) as Cost * MODPOW_COST_PER_BYTE_EXPONENT;
    check_cost(cost, max_cost)?;
    let (modulus, msize) = int_atom(a, modulus, "modpow")?;
    cost += (msize * msize) as Cost * MODPOW_COST_PER_BYTE_MOD;
    check_cost(cost, max_cost)?;

    if bsize > 256 || esize > 256 || msize > 256 {
        return Err(EvalErr::InvalidOpArg(input, "modpow".to_string()));
    }
```

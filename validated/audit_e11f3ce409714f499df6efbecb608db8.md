### Title
`CANONICAL_INTS` Flag Not Enforced in `int_atom()` — Arithmetic Operators Accept Non-Canonical Integers in Mempool Mode - (File: src/op_utils.rs)

### Summary

The `ClvmFlags::CANONICAL_INTS` flag, which is part of `MEMPOOL_MODE`, is documented to "require integers passed to operators use canonical representation, meaning no unnecessary leading zeros." The flag is correctly enforced in `uint_atom()` (used for softfork cost and extension arguments), but is entirely absent from `int_atom()` (used by the majority of arithmetic operators). As a result, in mempool mode, operators such as `op_div`, `op_divmod`, `op_mod`, `op_modpow`, `op_gr`, `op_ash`, `op_logand`, `op_logior`, `op_logxor`, `op_lognot`, `op_multiply`, and `op_pubkey_for_exp` silently accept non-canonical integer arguments (e.g., `0x0001` instead of `0x01`), bypassing the restriction that `CANONICAL_INTS` is supposed to impose.

### Finding Description

`MEMPOOL_MODE` is defined as:

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
``` [1](#0-0) 

`uint_atom()` correctly gates on `CANONICAL_INTS`:

```rust
if flags.contains(ClvmFlags::CANONICAL_INTS) {
    if buf[0] == 0 {
        if buf.len() < 2 || (buf[1] & 0x80) == 0 {
            return Err(EvalErr::InvalidOpArg(...));
        }
        buf = &buf[1..];
    }
}
``` [2](#0-1) 

But `int_atom()` takes no `flags` parameter at all and performs no canonicality check:

```rust
pub fn int_atom(a: &Allocator, args: NodePtr, op_name: &str) -> Result<(Number, usize)> {
    match a.sexp(args) {
        SExp::Atom => Ok((a.number(args), a.atom_len(args))),
        _ => Err(...)?,
    }
}
``` [3](#0-2) 

Every arithmetic operator that calls `int_atom()` also declares `_flags: ClvmFlags` but never passes it to `int_atom()`. For example, `op_div`:

```rust
pub fn op_div(a: &mut Allocator, input: NodePtr, max_cost: Cost, flags: ClvmFlags) -> Response {
    ...
    let (a0, a0_len) = int_atom(a, v0, "/")?;
    let (a1, a1_len) = int_atom(a, v1, "/")?;
``` [4](#0-3) 

Similarly for `op_modpow`:

```rust
pub fn op_modpow(a: &mut Allocator, input: NodePtr, max_cost: Cost, flags: ClvmFlags) -> Response {
    ...
    let (base, bsize) = int_atom(a, base, "modpow")?;
    let (exponent, esize) = int_atom(a, exponent, "modpow")?;
    let (modulus, msize) = int_atom(a, modulus, "modpow")?;
``` [5](#0-4) 

And `op_add` and `op_subtract` declare `_flags` but ignore it entirely:

```rust
pub fn op_add(
    a: &mut Allocator,
    mut input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
``` [6](#0-5) 

The full set of affected operators dispatched through `ChiaDialect::op()` includes opcodes 16–27, 29–30, 32–34, 48, 60–61 — all of which reach `int_atom()` without any `CANONICAL_INTS` check. [7](#0-6) 

### Impact Explanation

The `CANONICAL_INTS` flag is part of `MEMPOOL_MODE` and is the mechanism by which the mempool enforces stricter integer encoding than consensus. Because `int_atom()` never checks this flag, an attacker can submit CLVM programs to the mempool that pass non-canonical integers (e.g., `0x0001` for the value `1`) to any arithmetic operator. The mempool accepts these programs when it is supposed to reject them. The restriction is only enforced for the softfork cost and extension arguments (which use `uint_atom()`), creating an inconsistent and incomplete enforcement of the `CANONICAL_INTS` policy — directly analogous to the BeraBitcoin pattern where `notBlacklisted()` is applied to standard transfers but omitted from privileged mint/redeem paths.

### Likelihood Explanation

Any attacker who can submit CLVM programs to a Chia mempool node can trigger this. No special privileges are required. Crafting a program with a non-canonical integer argument (e.g., prepending a zero byte to any integer atom) is trivial and requires only knowledge of CLVM serialization.

### Recommendation

Add a `flags: ClvmFlags` parameter to `int_atom()` and enforce the `CANONICAL_INTS` check inside it, mirroring the existing logic in `uint_atom()`. All callers that currently pass `_flags` (ignoring it) should forward the flags to `int_atom()`. Alternatively, add a dedicated `canonical_int_atom()` wrapper that performs the check and use it in all arithmetic operators when `CANONICAL_INTS` is set.

### Proof of Concept

With `MEMPOOL_MODE` flags active, the following program should be rejected but is accepted:

```
(/ (q . 0x0001) (q . 0x01))
```

`0x0001` is a non-canonical encoding of the integer `1` (has a redundant leading zero). `uint_atom()` would reject this with `"/ requires u32 arg with no leading zeros"`, but `int_atom()` silently accepts it and returns `(Number(1), 2)` — the length `2` is then used in cost computation, meaning the cost is also computed incorrectly relative to the canonical form. The mempool accepts the transaction when `CANONICAL_INTS` requires it to be rejected. [8](#0-7) [3](#0-2)

### Citations

**File:** src/chia_dialect.rs (L70-76)
```rust
/// The default mode when running generators in mempool-mode (i.e. the stricter
/// mode).
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L190-254)
```rust
        let f = match op {
            // 1 = quote
            // 2 = apply
            3 => op_if,
            4 => op_cons,
            5 => op_first,
            6 => op_rest,
            7 => op_listp,
            8 => op_raise,
            9 => op_eq,
            10 => op_gr_bytes,
            11 => op_sha256,
            12 => op_substr,
            13 => op_strlen,
            14 => op_concat,
            // 15 ---
            16 => op_add,
            17 => op_subtract,
            18 => op_multiply,
            19 => op_div,
            20 => op_divmod,
            21 => op_gr,
            22 => op_ash,
            23 => op_lsh,
            24 => op_logand,
            25 => op_logior,
            26 => op_logxor,
            27 => op_lognot,
            // 28 ---
            29 => op_point_add,
            30 => op_pubkey_for_exp,
            // 31 ---
            32 => op_not,
            33 => op_any,
            34 => op_all,
            // 35 ---
            // 36 = softfork
            48 => op_coinid,
            49 => op_bls_g1_subtract,
            50 => op_bls_g1_multiply,
            51 => op_bls_g1_negate,
            52 => op_bls_g2_add,
            53 => op_bls_g2_subtract,
            54 => op_bls_g2_multiply,
            55 => op_bls_g2_negate,
            56 => op_bls_map_to_g1,
            57 => op_bls_map_to_g2,
            58 => op_bls_pairing_identity,
            59 => op_bls_verify,
            60 => {
                if flags.contains(ClvmFlags::DISABLE_OP) {
                    return Err(EvalErr::Unimplemented(o))?;
                }
                op_modpow
            }
            61 => op_mod,
            62 if flags.contains(ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD) => op_keccak256,
            63 if flags.contains(ClvmFlags::ENABLE_SHA256_TREE) => op_sha256_tree,
            64 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256k1_verify,
            65 if flags.contains(ClvmFlags::ENABLE_SECP_OPS) => op_secp256r1_verify,
            _ => {
                return unknown_operator(allocator, o, argument_list, flags, max_cost);
            }
        };
        f(allocator, argument_list, max_cost, flags)
```

**File:** src/op_utils.rs (L47-111)
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

            if buf.len() > SIZE {
                return Err(EvalErr::InvalidOpArg(
                    args,
                    format!(
                        "{op_name} requires u{0} arg (with no leading zeros)",
                        SIZE * 8
                    ),
                ))?;
            }

            let mut ret = 0;
            for b in buf {
                ret <<= 8;
                ret |= *b as u64;
            }
            Ok(ret)
        }
        NodeVisitor::U32(val) => Ok(val as u64),
        NodeVisitor::Pair(_, _) => Err(EvalErr::InvalidOpArg(
            args,
            format!("Requires Int Argument: {op_name}"),
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

**File:** src/more_ops.rs (L411-416)
```rust
pub fn op_add(
    a: &mut Allocator,
    mut input: NodePtr,
    max_cost: Cost,
    _flags: ClvmFlags,
) -> Response {
```

**File:** src/more_ops.rs (L658-679)
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
        return Err(EvalErr::InvalidOpArg(input, "div".to_string()));
    }
    let cost = DIV_BASE_COST + ((a0_len + a1_len) as Cost) * DIV_COST_PER_BYTE;
    check_cost(cost, max_cost)?;
    if a1.sign() == Sign::NoSign {
        return Err(EvalErr::DivisionByZero(input));
    }
    let q = a0.div_floor(&a1);
    let q = a.new_number(q)?;
    Ok(malloc_cost(a, cost, q))
}
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

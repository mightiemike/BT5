### Title
`MALACHITE` Arithmetic Flag Absent from `MEMPOOL_MODE` and Python API Creates Consensus/Mempool Divergence for `div`/`divmod`/`mod`/`modpow` — (File: `src/chia_dialect.rs`, `wheel/src/api.rs`)

---

### Summary

`ClvmFlags::MALACHITE` (bit `0x1000`) switches the arithmetic backend for `div`, `divmod`, `mod`, and `modpow` from `num-bigint` to `malachite-bigint`. This flag is absent from `MEMPOOL_MODE` and is not exported as a named constant to Python callers. If a consensus node enables `MALACHITE` while mempool validation runs without it, attacker-crafted CLVM programs using these operators on negative or large integers can produce divergent results between the two execution paths — a direct analog to the LSSVMRouter/VeryFastRouter feature-parity gap where one code path silently lacks a feature the other supports.

---

### Finding Description

**Vulnerability class:** Flag/operator wiring error — feature parity gap between execution paths.

**Root cause — three concrete locations:**

**1. Flag defined but excluded from `MEMPOOL_MODE`** [1](#0-0) 

```rust
/// Use malachite-bigint instead of num-bigint for div, divmod, mod, and modpow.
const MALACHITE = 0x1000;
``` [2](#0-1) 

```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

`MALACHITE` is not unioned into `MEMPOOL_MODE`. Mempool validation therefore always uses `num-bigint` semantics for `div`/`divmod`/`mod`/`modpow`, regardless of what consensus nodes enable.

**2. Flag not exported to Python callers** [3](#0-2) 

```rust
m.add("NO_UNKNOWN_OPS", ClvmFlags::NO_UNKNOWN_OPS.bits())?;
m.add("LIMIT_HEAP", ClvmFlags::LIMIT_HEAP.bits())?;
m.add("MEMPOOL_MODE", MEMPOOL_MODE.bits())?;
m.add("ENABLE_SHA256_TREE", ClvmFlags::ENABLE_SHA256_TREE.bits())?;
m.add("ENABLE_SECP_OPS", ClvmFlags::ENABLE_SECP_OPS.bits())?;
m.add("DISABLE_OP", ClvmFlags::DISABLE_OP.bits())?;
m.add("CANONICAL_INTS", ClvmFlags::CANONICAL_INTS.bits())?;
```

`MALACHITE` (0x1000) is absent. Python callers invoking `run_serialized_chia_program` have no documented way to enable `MALACHITE` semantics. While `ClvmFlags::from_bits_truncate` would accept a raw `0x1000` value, no Python-facing constant is provided, making it invisible to downstream integrators. [4](#0-3) 

**3. Divergent arithmetic backend wired through `malachite_int_atom`** [5](#0-4) 

```rust
pub fn malachite_int_atom(
    a: &Allocator,
    args: NodePtr,
    op_name: &str,
) -> Result<(Malachite, usize)> { ... }
```

This function is the entry point for `MALACHITE`-path arithmetic. When `MALACHITE` is set, `div`/`divmod`/`mod`/`modpow` use `malachite-bigint`; when unset, they use `num-bigint`. The two libraries can differ in rounding semantics for negative-number floor division (e.g., `(-1) / 2` → `-1` under floor vs. `0` under truncation), producing different atom bytes and therefore different tree hashes.

---

### Impact Explanation

If a consensus node runs with `MALACHITE` enabled and a mempool node runs `MEMPOOL_MODE` (which excludes `MALACHITE`), the same CLVM puzzle/solution pair can:

- **Pass mempool validation** (num-bigint result accepted, cost within limit)
- **Produce a different output atom** in consensus execution (malachite-bigint result differs)

This breaks the invariant that mempool pre-validation is a sound under-approximation of consensus execution. An attacker can craft a coin puzzle whose solution exercises `div`/`divmod`/`mod`/`modpow` on negative or boundary integers, causing the spend to be accepted into the mempool but rejected (or accepted with a different output) at the consensus layer — a consensus/mempool divergence.

The corrupted result is the concrete `NodePtr` atom returned by the arithmetic operator, which propagates into the puzzle output, the AGG_SIG conditions list, and ultimately the coin spend validity decision.

---

### Likelihood Explanation

**Medium.** The `MALACHITE` flag exists in the production `ClvmFlags` bitset and is wired to real operator dispatch in `src/more_ops.rs`. Its omission from `MEMPOOL_MODE` is not annotated as intentional. Any deployment where consensus nodes enable `MALACHITE` (e.g., as part of a hard-fork activation) while mempool nodes continue using `MEMPOOL_MODE` verbatim will exhibit the divergence. The attacker-controlled entry path is straightforward: submit a serialized CLVM program to `run_serialized_chia_program` or the mempool that exercises `div`/`divmod`/`mod`/`modpow` with negative operands.

---

### Recommendation

1. **Add `MALACHITE` to `MEMPOOL_MODE`** if malachite-bigint semantics are intended for consensus, so mempool validation is consistent:
   ```rust
   pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
       .union(ClvmFlags::LIMIT_HEAP)
       .union(ClvmFlags::DISABLE_OP)
       .union(ClvmFlags::CANONICAL_INTS)
       .union(ClvmFlags::LIMIT_SOFTFORK)
       .union(ClvmFlags::MALACHITE); // add this
   ```
2. **Export `MALACHITE` to Python** in `wheel/src/api.rs` so downstream callers can opt in:
   ```rust
   m.add("MALACHITE", ClvmFlags::MALACHITE.bits())?;
   ```
3. **Document** whether `MALACHITE` is a consensus flag or a local optimization, and add a test asserting that `MEMPOOL_MODE` and the consensus flag set agree on arithmetic results for negative-number edge cases.

---

### Proof of Concept

A CLVM program such as `(/ (q . -1) (q . 2))` evaluated twice — once with `flags = MEMPOOL_MODE` (no `MALACHITE`) and once with `flags = MEMPOOL_MODE | 0x1000` (`MALACHITE` enabled) — will return different atom bytes if `num-bigint` and `malachite-bigint` differ in floor-division rounding for negative dividends. The divergent `NodePtr` atom propagates as the puzzle output, causing the two execution paths to disagree on the spend's validity or output conditions.

Attacker entry path:
1. Craft serialized CLVM bytes for `(/ (q . -1) (q . 2))`.
2. Submit to `run_serialized_chia_program(program, args, max_cost, MEMPOOL_MODE)` — mempool accepts result A.
3. Consensus node runs same bytes with `MALACHITE` enabled — produces result B ≠ A.
4. Spend passes mempool but diverges at consensus, breaking chain agreement. [1](#0-0) [2](#0-1) [3](#0-2) [5](#0-4)

### Citations

**File:** src/chia_dialect.rs (L65-67)
```rust
        /// Use malachite-bigint instead of num-bigint for div, divmod, mod, and modpow.
        const MALACHITE = 0x1000;
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

**File:** wheel/src/api.rs (L317-324)
```rust
    m.add("NO_UNKNOWN_OPS", ClvmFlags::NO_UNKNOWN_OPS.bits())?;
    m.add("LIMIT_HEAP", ClvmFlags::LIMIT_HEAP.bits())?;
    m.add("MEMPOOL_MODE", MEMPOOL_MODE.bits())?;
    m.add("ENABLE_SHA256_TREE", ClvmFlags::ENABLE_SHA256_TREE.bits())?;
    m.add("ENABLE_SECP_OPS", ClvmFlags::ENABLE_SECP_OPS.bits())?;
    m.add("DISABLE_OP", ClvmFlags::DISABLE_OP.bits())?;
    m.add("CANONICAL_INTS", ClvmFlags::CANONICAL_INTS.bits())?;
    m.add_class::<LazyNode>()?;
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

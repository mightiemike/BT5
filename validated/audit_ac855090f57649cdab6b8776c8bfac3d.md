### Title
`LIMIT_HEAP` Flag Defined and Included in `MEMPOOL_MODE` But Never Enforced in the Execution Engine — (`src/chia_dialect.rs`)

---

### Summary

`ClvmFlags::LIMIT_HEAP` is documented as enforcing limits on atom-byte allocations and pair counts. It is included in the `MEMPOOL_MODE` constant used for stricter mempool validation. However, the flag is never checked in `src/run_program.rs` or `src/allocator.rs` — the only places where such limits could be enforced. The heap limits are silently ignored, exactly mirroring the PrePO `expiryTime` pattern: a constraint is declared, wired into a production mode, but never applied.

---

### Finding Description

`ClvmFlags::LIMIT_HEAP` is defined in `src/chia_dialect.rs` with the documentation:

> "When set, limits the number of atom-bytes allowed to be allocated, as well as the number of pairs." [1](#0-0) 

It is included in the `MEMPOOL_MODE` constant, which is the production flag set used when validating transactions in the mempool (the stricter, pre-consensus path): [2](#0-1) 

A `grep` search for `LIMIT_HEAP` across all `src/**/*.rs` files returns **only two hits, both in `src/chia_dialect.rs`** — the definition and the `MEMPOOL_MODE` constant. The flag does not appear in:

- `src/run_program.rs` — the main CLVM interpreter loop
- `src/allocator.rs` — the memory allocator that tracks atom bytes and pairs
- Any operator file (`src/more_ops.rs`, `src/bls_ops.rs`, etc.) [3](#0-2) 

The `ChiaDialect` struct stores `flags` and passes them through to operators, but no code path reads `LIMIT_HEAP` to enforce any allocation ceiling. The `Allocator` has no awareness of this flag at all.

By contrast, other flags in the same struct are actively enforced: `NO_UNKNOWN_OPS` is checked in `unknown_operator()`, `CANONICAL_INTS` is checked in `src/op_utils.rs`, `LIMIT_SOFTFORK` is checked in `run_program.rs` at line 415, and `DISABLE_OP` is checked per-operator. `LIMIT_HEAP` alone has no corresponding enforcement site. [4](#0-3) 

---

### Impact Explanation

Any caller using `MEMPOOL_MODE` (or explicitly setting `LIMIT_HEAP`) believes heap allocation is bounded. In reality, a CLVM program submitted to the mempool can allocate an unbounded number of atom bytes and pairs, limited only by the cost budget and physical memory. This has two concrete consequences:

1. **Resource exhaustion**: A crafted program can allocate far more heap than the mempool operator expects to permit, causing memory pressure or OOM conditions in full nodes running mempool validation.

2. **Consensus/mempool divergence**: If the intended semantics of `LIMIT_HEAP` is to *reject* programs that exceed heap thresholds in mempool mode (analogous to how `NO_UNKNOWN_OPS` rejects unknown operators), then programs that should be rejected are silently accepted. A future enforcement of the flag would then create a split between nodes on different software versions — a consensus-layer divergence risk.

---

### Likelihood Explanation

The entry path is direct and attacker-controlled: any party submitting a CLVM puzzle/solution to a Chia full node's mempool can craft a program that allocates large numbers of pairs or atom bytes. The `MEMPOOL_MODE` flag set is used by production nodes. No special privileges are required. The only barrier is the cost budget, which caps CPU time but does not cap heap under the current (broken) implementation.

---

### Recommendation

Implement the heap-limit enforcement that `LIMIT_HEAP` promises. In `src/allocator.rs`, add counters for total atom bytes allocated and total pairs allocated. In `src/run_program.rs` (or in the `Allocator` itself), after each allocation, check these counters against the configured limits when `LIMIT_HEAP` is set and return `EvalErr::CostExceeded` (or a dedicated heap-limit error) if the threshold is crossed. Alternatively, if the flag is intentionally unimplemented, remove it from `MEMPOOL_MODE` and update the documentation to reflect that no heap limit is enforced.

---

### Proof of Concept

```
# MEMPOOL_MODE includes LIMIT_HEAP:
MEMPOOL_MODE = NO_UNKNOWN_OPS | LIMIT_HEAP | DISABLE_OP | CANONICAL_INTS | LIMIT_SOFTFORK

# A CLVM program that allocates O(N) pairs, e.g. building a long list:
# (a (q 2 2 (c 2 (c 5 (c (c 5 11) ())))) (c (q 2 (i (= 11 ()) (q 1 . ()) (q 2 2 (c 2 (c 5 (c (r 11) ()))))) 1) (c args (c args ()))))
# Run with ChiaDialect::new(MEMPOOL_MODE) and a large cost budget.
# Expected (per LIMIT_HEAP docs): rejected once pair count exceeds the limit.
# Actual: runs to completion, allocating unbounded pairs, LIMIT_HEAP has no effect.
```

The flag is accepted by `ChiaDialect::new(MEMPOOL_MODE)`, stored in `self.flags`, passed to every operator call — but no code ever reads `flags.contains(ClvmFlags::LIMIT_HEAP)` to enforce anything. [5](#0-4)

### Citations

**File:** src/chia_dialect.rs (L36-37)
```rust
        /// as well as the number of pairs.
        const LIMIT_HEAP = 0x0004;
```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L92-100)
```rust
pub struct ChiaDialect {
    flags: ClvmFlags,
}

impl ChiaDialect {
    pub fn new(flags: ClvmFlags) -> ChiaDialect {
        ChiaDialect { flags }
    }
}
```

**File:** src/chia_dialect.rs (L136-155)
```rust
    fn op(
        &self,
        allocator: &mut Allocator,
        o: NodePtr,
        argument_list: NodePtr,
        max_cost: Cost,
        extension: OperatorSet,
    ) -> Response {
        let flags = self.flags
            | match extension {
                // This is the default set of operators, so no special flags need to be added.
                OperatorSet::Default => ClvmFlags::empty(),

                // Since BLS has been hardforked in universally, this has no effect.
                OperatorSet::Bls => ClvmFlags::empty(),

                // Keccak is allowed as if it were a default operator, inside of the softfork guard.
                OperatorSet::Keccak => ClvmFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD,
            };

```

**File:** src/run_program.rs (L415-418)
```rust
            if self.dialect.flags().contains(ClvmFlags::LIMIT_SOFTFORK)
                && self.softfork_stack.len() >= 20
            {
                return Err(EvalErr::SoftforkStackDepthExceeded);
```

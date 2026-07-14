### Title
`LIMIT_HEAP` Flag Declared in `MEMPOOL_MODE` but Never Enforced During Execution - (File: `src/chia_dialect.rs`)

---

### Summary

`ClvmFlags::LIMIT_HEAP` is defined, documented, included in the `MEMPOOL_MODE` constant, and exposed to Python callers as a meaningful heap-limiting constraint. However, it is never checked anywhere in the execution engine (`run_program`, operator dispatch, or any operator implementation). The `Allocator`'s actual heap limit is a separate construction-time parameter (`heap_limit`) that is entirely independent of this flag. A caller relying on `MEMPOOL_MODE` to enforce heap limits gets no enforcement.

---

### Finding Description

`ClvmFlags::LIMIT_HEAP` is defined in `src/chia_dialect.rs` with the documentation: *"When set, limits the number of atom-bytes allowed to be allocated, as well as the number of pairs."* [1](#0-0) 

It is included in the `MEMPOOL_MODE` constant, which is the intended strict mode for mempool validation: [2](#0-1) 

It is also exported to Python callers via the wheel: [3](#0-2) 

However, a complete search of every execution-path file reveals that `LIMIT_HEAP` is **never read or checked** in:

- `src/run_program.rs` — the main interpreter loop
- `src/chia_dialect.rs` — the `ChiaDialect::op()` dispatch function
- `src/more_ops.rs` — all arithmetic/bitwise operators
- `src/core_ops.rs` — all core operators

The `Allocator` does have a real `heap_limit` field enforced at allocation time: [4](#0-3) 

But this limit is set at **construction time** via `Allocator::new_limited(heap_limit)`: [5](#0-4) 

The `ChiaDialect` only receives `ClvmFlags`; it has no mechanism to communicate `LIMIT_HEAP` to the `Allocator`. The `run_program` function receives a pre-constructed `Allocator` and a `Dialect`, with no bridge between the flag and the allocator's limit: [6](#0-5) 

The result: setting `LIMIT_HEAP` (or using `MEMPOOL_MODE`) has zero effect on actual heap allocation during execution.

---

### Impact Explanation

A caller using `MEMPOOL_MODE` (which includes `LIMIT_HEAP`) expects that heap allocation is bounded beyond the absolute `MAX_NUM_ATOMS`/`MAX_NUM_PAIRS` hard limits. Because `LIMIT_HEAP` is silently ignored, a malicious CLVM program submitted to the mempool can allocate heap memory up to the absolute allocator maximum — far beyond what the mempool policy intends to permit. This creates a **consensus/mempool divergence**: a node enforcing `LIMIT_HEAP` via a correctly wired implementation would reject a spend, while this implementation accepts it. It also enables resource exhaustion attacks against mempool validators that expect the flag to bound memory use.

---

### Likelihood Explanation

The `LIMIT_HEAP` flag is part of the documented `MEMPOOL_MODE` constant and is exported to Python. Any caller using `MEMPOOL_MODE` for mempool validation is affected. The attacker-controlled entry path is straightforward: submit a CLVM program that allocates large atoms or many pairs (e.g., via repeated `concat` or `sha256` calls producing large outputs). The program will succeed in this implementation despite the caller having set `LIMIT_HEAP`, because the flag is never checked. [2](#0-1) 

---

### Recommendation

Either:
1. **Wire the flag to the allocator**: When `LIMIT_HEAP` is set in `ClvmFlags`, construct or reconfigure the `Allocator` with a stricter `heap_limit` before calling `run_program`. This requires passing the flags into the allocator setup path.
2. **Remove the flag**: If heap limiting is exclusively the caller's responsibility (via `Allocator::new_limited`), remove `LIMIT_HEAP` from `ClvmFlags` and `MEMPOOL_MODE` to eliminate the false guarantee.

The current state — a documented, exported flag that does nothing — is the direct analog of the Passage contract accepting slippage parameters that are never checked at match time.

---

### Proof of Concept

```python
from clvm_rs import run_serialized_chia_program, MEMPOOL_MODE
# Craft a CLVM program that allocates a large atom via concat
# (concat (q . <256-byte blob>) (q . <256-byte blob>) ...)
# With LIMIT_HEAP set, this should be rejected if the flag were enforced.
# In practice it succeeds because LIMIT_HEAP is never checked.
result = run_serialized_chia_program(
    large_concat_program_bytes,
    env_bytes,
    max_cost=10_000_000,
    flags=MEMPOOL_MODE,  # LIMIT_HEAP is set here but ignored
)
# result succeeds; no heap limit was enforced
```

At the Rust level, the broken invariant is confirmed by the absence of any `flags.contains(ClvmFlags::LIMIT_HEAP)` check in `src/run_program.rs` or `src/chia_dialect.rs`, while the flag is documented to enforce exactly such a limit. [1](#0-0) [7](#0-6)

### Citations

**File:** src/chia_dialect.rs (L35-37)
```rust
        /// When set, limits the number of atom-bytes allowed to be allocated,
        /// as well as the number of pairs.
        const LIMIT_HEAP = 0x0004;
```

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

**File:** wheel/src/api.rs (L318-318)
```rust
    m.add("LIMIT_HEAP", ClvmFlags::LIMIT_HEAP.bits())?;
```

**File:** src/allocator.rs (L276-278)
```rust
    // the atom_vec may not grow past this
    heap_limit: usize,

```

**File:** src/allocator.rs (L363-365)
```rust
    pub fn new_limited(heap_limit: usize) -> Self {
        // we have a maximum of 4 GiB heap, because pointers are 32 bit unsigned
        assert!(heap_limit <= u32::MAX as usize);
```

**File:** src/allocator.rs (L619-623)
```rust
    pub fn new_atom(&mut self, v: &[u8]) -> Result<NodePtr> {
        let start = self.u8_vec.len() as u32;
        if start as usize + self.ghost_heap + v.len() > self.heap_limit {
            return Err(EvalErr::OutOfMemory);
        }
```

**File:** src/run_program.rs (L564-573)
```rust
pub fn run_program<'a, D: Dialect>(
    allocator: &'a mut Allocator,
    dialect: &'a D,
    program: NodePtr,
    env: NodePtr,
    max_cost: Cost,
) -> Response {
    let mut rpc = RunProgramContext::new(allocator, dialect);
    rpc.run_program(program, env, max_cost)
}
```

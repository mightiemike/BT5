### Title
`gc_candidate` Match Arm Silently Omits bls_g2_* Operators, Bypassing GC for Those Opcodes — (File: `src/chia_dialect.rs`)

---

### Summary

The `gc_candidate` function in `ChiaDialect` contains a match arm that is supposed to enumerate every operator eligible for allocator checkpoint/restore under the `ENABLE_GC` flag. The inline comment explicitly names `bls_g2_add`, `bls_g2_subtract`, `bls_g2_multiply`, and `bls_g2_negate` (opcodes 52–55) as members of that set, but the actual match arm jumps directly from `51` to `56`, silently omitting all four. This is a direct analog to the reported bug class: a caller-visible specification (the comment) and an internal dispatch table (the match arm) are wired to different sets of operators, causing the intended behavior to be silently skipped for attacker-reachable inputs.

---

### Finding Description

In `src/chia_dialect.rs`, the `gc_candidate` method reads:

```rust
fn gc_candidate(&self, allocator: &Allocator, op: NodePtr) -> bool {
    if !self.flags.contains(ClvmFlags::ENABLE_GC) {
        return false;
    }
    // apply listp eq gr_bytes sha256 strlen add subtract multiply
    // div divmod gr ash lsh logand logior logxor lognot point_add
    // pubkey_for_exp not any all coinid bls_g1_subtract
    // bls_g1_multiply bls_g1_negate bls_g2_add bls_g2_subtract
    // bls_g2_multiply bls_g2_negate bls_map_to_g1
    // bls_pairing_identity bls_verify modpow mod keccak256
    // sha256_tree
    match allocator.node(op) {
        NodeVisitor::U32(
            2 | 7 | 9 | 10 | 11 | 13 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 | 24 | 25 | 26
            | 27 | 29 | 30 | 32 | 33 | 34 | 48 | 49 | 50 | 51 | 56 | 58 | 59 | 60 | 61 | 62
            | 63,
        ) => true,
        _ => false,
    }
}
``` [1](#0-0) 

The comment names these operators as GC candidates:

| Opcode | Operator |
|--------|----------|
| 52 | `bls_g2_add` |
| 53 | `bls_g2_subtract` |
| 54 | `bls_g2_multiply` |
| 55 | `bls_g2_negate` |

The match arm goes `… | 51 | 56 | …`, skipping 52, 53, 54, 55 entirely. The corresponding operator dispatch table confirms these opcodes are live and reachable: [2](#0-1) 

The bls_g1_* counterparts (49, 50, 51) are correctly present in the match arm, making the omission of the bls_g2_* group structurally inconsistent and not a deliberate design choice.

---

### Impact Explanation

When `ENABLE_GC` is set, the execution engine calls `gc_candidate` before dispatching each operator. If it returns `true`, the allocator state is checkpointed; after the operator returns, if the result is nil or one, the checkpoint is restored, freeing all intermediate heap allocations made during that operator's execution. Because `gc_candidate` returns `false` for opcodes 52–55, the allocator is never checkpointed before these operators run. Any intermediate G2-point or scratch allocations they produce accumulate on the heap and are never reclaimed by the GC path.

Concrete corrupted invariant: `gc_candidate(op=52..=55)` returns `false` when `ENABLE_GC` is set, contradicting the documented specification in the same function. The allocator checkpoint that should gate these operators is never created, so the restore path is unreachable for them regardless of their output.

Downstream effect: nodes running with `ENABLE_GC` enabled will accumulate heap allocations for every `bls_g2_*` call that the GC was supposed to reclaim. Under `LIMIT_HEAP`, a crafted program that chains many `bls_g2_add`/`bls_g2_subtract`/`bls_g2_multiply`/`bls_g2_negate` calls can exhaust the heap budget faster than the same program would on a node without `ENABLE_GC`, because the GC that was supposed to keep peak usage low is silently inoperative for these four opcodes. This creates a divergence in program acceptance between nodes with and without `ENABLE_GC` enabled.

---

### Likelihood Explanation

`ENABLE_GC` is not part of `MEMPOOL_MODE` by default: [3](#0-2) 

It must be explicitly opted into. Nodes that do enable it (e.g., for memory-efficiency in high-throughput environments) are exposed. An attacker who knows a target node has `ENABLE_GC` set can craft a CLVM program consisting of many `bls_g2_*` operations to drive heap usage above what the GC was supposed to prevent, potentially causing the node to reject programs that a non-GC node would accept. Likelihood is **low** in the general case but **medium** for any deployment that explicitly enables `ENABLE_GC`.

---

### Recommendation

Add opcodes 52, 53, 54, and 55 to the `gc_candidate` match arm to match the documented specification:

```rust
NodeVisitor::U32(
    2 | 7 | 9 | 10 | 11 | 13 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 | 24 | 25 | 26
    | 27 | 29 | 30 | 32 | 33 | 34 | 48 | 49 | 50 | 51 | 52 | 53 | 54 | 55 | 56 | 58
    | 59 | 60 | 61 | 62 | 63,
) => true,
```

Add a unit test that asserts `gc_candidate` returns `true` for every opcode named in the comment when `ENABLE_GC` is set, so future edits to either the comment or the match arm are caught immediately.

---

### Proof of Concept

**Trigger**: Compile with `ENABLE_GC` flag set. Execute any CLVM program that calls `bls_g2_add` (opcode 52) in a loop.

**Expected**: `gc_candidate` returns `true` for opcode 52; allocator is checkpointed before each call; intermediate allocations are freed after each call.

**Actual**: `gc_candidate` returns `false` for opcode 52 (and 53, 54, 55); no checkpoint is created; intermediate allocations accumulate on the heap for the lifetime of the program.

**Verification**: Insert a debug assertion `assert!(dialect.gc_candidate(&allocator, op_52_node))` after constructing a `ChiaDialect` with `ENABLE_GC`; it will fire, confirming the mismatch.

### Citations

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L114-133)
```rust
    fn gc_candidate(&self, allocator: &Allocator, op: NodePtr) -> bool {
        if !self.flags.contains(ClvmFlags::ENABLE_GC) {
            return false;
        }
        // apply listp eq gr_bytes sha256 strlen add subtract multiply
        // div divmod gr ash lsh logand logior logxor lognot point_add
        // pubkey_for_exp not any all coinid bls_g1_subtract
        // bls_g1_multiply bls_g1_negate bls_g2_add bls_g2_subtract
        // bls_g2_multiply bls_g2_negate bls_map_to_g1
        // bls_pairing_identity bls_verify modpow mod keccak256
        // sha256_tree
        #[allow(clippy::match_like_matches_macro)]
        match allocator.node(op) {
            NodeVisitor::U32(
                2 | 7 | 9 | 10 | 11 | 13 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 | 24 | 25 | 26
                | 27 | 29 | 30 | 32 | 33 | 34 | 48 | 49 | 50 | 51 | 56 | 58 | 59 | 60 | 61 | 62
                | 63,
            ) => true,
            _ => false,
        }
```

**File:** src/chia_dialect.rs (L231-235)
```rust
            52 => op_bls_g2_add,
            53 => op_bls_g2_subtract,
            54 => op_bls_g2_multiply,
            55 => op_bls_g2_negate,
            56 => op_bls_map_to_g1,
```

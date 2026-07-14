### Title
Stale BLS Point Validation Cache Not Cleared on Transparent Checkpoint Restore, Enabling Cross-Epoch Cache Aliasing - (File: src/allocator.rs)

### Summary
The `Allocator` maintains `validated_g1_points` and `validated_g2_points` caches (keyed by `NodePtr`) to skip expensive BLS point deserialization/validation on repeated use. When `restore_transparent_checkpoint` is called — which the GC-candidate mechanism invokes during program execution — `atom_vec` is truncated, making atom indices available for reuse. The validation caches are **not** cleared during this restore. A subsequent allocation at the same index produces a `NodePtr` with identical bits to the previously-validated (now-freed) atom. Any BLS operator that calls `a.g1()` or `a.g2()` on this new `NodePtr` will find a stale cache hit and skip validation, treating the new (potentially invalid or attacker-controlled) bytes as an already-validated BLS point.

### Finding Description

The `Allocator` struct holds two `HashSet<NodePtr>` caches: [1](#0-0) 

`restore_transparent_checkpoint` truncates `atom_vec`, `pair_vec`, and `u8_vec` back to the checkpoint watermarks, but does **not** touch either validation cache: [2](#0-1) 

`restore_checkpoint` delegates to `restore_transparent_checkpoint` and also does not clear the caches: [3](#0-2) 

The GC-candidate mechanism in `eval_op_atom` takes a `transparent_checkpoint` before dispatching any operator flagged as a GC candidate, then calls `maybe_restore_with_node` afterward: [4](#0-3) 

`maybe_restore_with_node` calls `restore_transparent_checkpoint` when savings exceed `MIN_SAVINGS = 1024` bytes: [5](#0-4) 

BLS operators — `bls_g1_subtract` (49), `bls_g1_multiply` (50), `bls_g1_negate` (51), `bls_pairing_identity` (58), `bls_verify` (59) — are all declared GC candidates: [6](#0-5) 

Each of these operators calls `a.g1(arg)` or `a.g2(arg)`, which checks `validated_g1_points` / `validated_g2_points` before performing the expensive deserialization. After a transparent checkpoint restore, the atom slot freed by the restore is reused by the next `new_atom` call, producing a `NodePtr` with the same 32-bit representation as the previously-validated (now-freed) atom. The cache lookup succeeds on this new `NodePtr`, and validation is skipped for bytes that were never validated.

`clear_validation_caches()` is only called at the very end of `run_program`, not during mid-execution restores: [7](#0-6) 

### Impact Explanation

Within a single `run_program` call with `ENABLE_GC` set:

1. A valid G1 point atom is allocated at index `N`; `a.g1()` validates it and inserts `NodePtr(Bytes, N)` into `validated_g1_points`.
2. A GC-candidate BLS operator executes, accumulating ≥ 1024 bytes of intermediate allocations; `maybe_restore_with_node` calls `restore_transparent_checkpoint`, truncating `atom_vec` back past index `N`.
3. An attacker-controlled atom with **invalid or different** G1 bytes is allocated at index `N`, producing the same `NodePtr(Bytes, N)`.
4. A subsequent call to `bls_verify` or `bls_pairing_identity` passes this new atom; `a.g1()` finds `NodePtr(Bytes, N)` in the cache and skips validation.

The corrupted result is: `bls_verify` or `bls_pairing_identity` operates on a G1 element deserialized from bytes that were never validated, potentially returning a wrong boolean (false positive signature acceptance or false negative) or causing undefined behavior in the BLS library if the bytes are structurally invalid.

### Likelihood Explanation

**Low-to-medium.** The `ENABLE_GC` flag must be set by the caller; it is absent from `MEMPOOL_MODE`: [8](#0-7) 

The GC restore requires ≥ 1024 bytes of intermediate allocations, which is achievable with multi-argument BLS operators. The attacker must control the CLVM program bytes and arrange the allocation sequence precisely. If `ENABLE_GC` is enabled in any consensus or wallet path, the impact rises to high (forged BLS signature acceptance).

### Recommendation

Clear `validated_g1_points` and `validated_g2_points` inside `restore_transparent_checkpoint` (and by extension `restore_checkpoint`), immediately after truncating `atom_vec`. Alternatively, key the caches on `(NodePtr, allocation_epoch)` where the epoch counter increments on every restore, so stale entries are never matched after a restore.

### Proof of Concept

```
; ENABLE_GC flag set on ChiaDialect
; Step 1: allocate a valid G1 point P_valid and call bls_g1_negate on it.
;         bls_g1_negate is a GC candidate; if intermediate allocs >= 1024 bytes,
;         restore_transparent_checkpoint fires, freeing P_valid's atom slot (index N).
; Step 2: allocate P_invalid (48 bytes of attacker-chosen garbage) at index N.
; Step 3: call (bls_verify sig P_invalid msg).
;         a.g1(P_invalid) finds NodePtr(Bytes, N) in validated_g1_points → skips validation.
;         bls_verify proceeds with unvalidated bytes, producing wrong result.
```

The exact corrupted value is the boolean result of `bls_verify` or `bls_pairing_identity` operating on an unvalidated G1/G2 element whose `NodePtr` index aliases a previously-validated (now-freed) atom slot. [3](#0-2) [9](#0-8) [10](#0-9)

### Citations

**File:** src/allocator.rs (L378-379)
```rust
            validated_g1_points: HashSet::new(),
            validated_g2_points: HashSet::new(),
```

**File:** src/allocator.rs (L465-470)
```rust
    pub fn restore_checkpoint(&mut self, cp: &Checkpoint) {
        self.restore_transparent_checkpoint(&cp.inner);
        self.ghost_atoms = cp.ghost_atoms;
        self.ghost_pairs = cp.ghost_pairs;
        self.ghost_heap = cp.ghost_heap;
    }
```

**File:** src/allocator.rs (L485-505)
```rust
    pub fn restore_transparent_checkpoint(&mut self, cp: &TransparentCheckpoint) {
        // if any of these asserts fire, it means we're trying to restore to
        // a state that has already been "long-jumped" passed (via another
        // restore to an earlier state). You can only restore backwards in time,
        // not forwards.
        assert!(self.u8_vec.len() >= cp.u8s as usize);
        assert!(self.pair_vec.len() >= cp.pairs as usize);
        assert!(self.atom_vec.len() >= cp.atoms as usize);
        self.ghost_heap += self.u8_vec.len() - cp.u8s as usize;
        self.ghost_pairs += self.pair_vec.len() - cp.pairs as usize;
        self.ghost_atoms += self.atom_vec.len() - cp.atoms as usize;
        self.u8_vec.truncate(cp.u8s as usize);
        self.pair_vec.truncate(cp.pairs as usize);
        self.atom_vec.truncate(cp.atoms as usize);

        // This invalidates all NodePtrs with higher index than this, with a
        // lower version than self.versions.len()
        #[cfg(feature = "allocator-debug")]
        self.versions
            .push((self.atom_vec.len() as u32, self.pair_vec.len() as u32));
    }
```

**File:** src/allocator.rs (L556-566)
```rust
        let saved_bytes = (self.u8_vec.len() - checkpoint.u8s as usize)
            + (self.atom_vec.len() - checkpoint.atoms as usize) * 8
            + (self.pair_vec.len() - checkpoint.pairs as usize) * 8;
        if saved_bytes < MIN_SAVINGS {
            return Ok(MaybeRestore::Aborted);
        }

        match self.checkpoint_node_status(checkpoint, ret) {
            NodeStatus::Before => {
                self.restore_transparent_checkpoint(checkpoint);
                Ok(MaybeRestore::NoReplace)
```

**File:** src/run_program.rs (L244-249)
```rust
            if self.dialect.gc_candidate(self.allocator, operator_node) {
                self.allocator_stack
                    .push(self.allocator.transparent_checkpoint());
                self.op_stack.push(Operation::RestoreAllocator);
                self.account_op_push();
            }
```

**File:** src/run_program.rs (L527-548)
```rust
                Operation::RestoreAllocator => {
                    let Some(checkpoint) = self.allocator_stack.pop() else {
                        return Err(EvalErr::InternalError(
                            NodePtr::NIL,
                            "allocator checkpoint stack empty".to_string(),
                        ));
                    };
                    let Some(&top) = self.val_stack.last() else {
                        return Err(EvalErr::InternalError(
                            NodePtr::NIL,
                            "value stack empty".to_string(),
                        ));
                    };
                    match self.allocator.maybe_restore_with_node(&checkpoint, top)? {
                        MaybeRestore::NoReplace => {}
                        MaybeRestore::Replace(new_node) => {
                            self.val_stack.pop().unwrap();
                            self.val_stack.push(new_node);
                        }
                        MaybeRestore::Aborted => {}
                    }
                    0
```

**File:** src/run_program.rs (L559-560)
```rust
        self.allocator.clear_validation_caches();
        Ok(Reduction(cost, self.pop()?))
```

**File:** src/chia_dialect.rs (L72-76)
```rust
pub const MEMPOOL_MODE: ClvmFlags = ClvmFlags::NO_UNKNOWN_OPS
    .union(ClvmFlags::LIMIT_HEAP)
    .union(ClvmFlags::DISABLE_OP)
    .union(ClvmFlags::CANONICAL_INTS)
    .union(ClvmFlags::LIMIT_SOFTFORK);
```

**File:** src/chia_dialect.rs (L127-132)
```rust
            NodeVisitor::U32(
                2 | 7 | 9 | 10 | 11 | 13 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 | 24 | 25 | 26
                | 27 | 29 | 30 | 32 | 33 | 34 | 48 | 49 | 50 | 51 | 56 | 58 | 59 | 60 | 61 | 62
                | 63,
            ) => true,
            _ => false,
```

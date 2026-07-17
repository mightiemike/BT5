### Title
Child `ChunkExtra.proposed_split` Not Reset to `None` After Shard Split — (`chain/chain/src/resharding/manager.rs`)

### Summary

During dynamic resharding, `process_memtrie_resharding_storage_update()` creates child `ChunkExtra` records by cloning the parent's `ChunkExtra` but never resets the `proposed_split` field to `None`. The child shards inherit the parent's non-`None` `proposed_split` value. When the first chunk of each child shard is produced in the new epoch, the chunk producer computes `proposed_split = None` (because the resharding cooldown prevents immediate re-splitting), but the stored child `ChunkExtra.proposed_split()` is still the parent's split value. The mismatch causes `validate_chunk_with_chunk_extra_and_receipts_root` to reject every first child-shard chunk with `InvalidChunkHeaderShardSplit`, halting block production for those shards.

### Finding Description

In `process_memtrie_resharding_storage_update()`, the child `ChunkExtra` is built by cloning the parent and updating only two fields:

```rust
// TODO(resharding): set all fields of `ChunkExtra`. Consider stronger
// typing. Clarify where it should happen when `State` and
// `FlatState` update is implemented.
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
``` [1](#0-0) 

The `proposed_split` field introduced in `ChunkExtraV5` is never touched:

```rust
pub struct ChunkExtraV5 {
    ...
    /// Proposed split of this shard (dynamic resharding).
    pub proposed_split: Option<TrieSplit>,
}
``` [2](#0-1) 

The parent shard's last chunk has `proposed_split = Some(TrieSplit { boundary_account, left_memory, right_memory })` — the very split that triggered the resharding. That value is silently inherited by both child `ChunkExtra` records and committed to the store.

When the first chunk of a child shard is produced in epoch N+1:

1. `compute_proposed_split` is called. `can_reshard` returns `false` because the resharding cooldown (`min_epochs_between_resharding > 0`) has not elapsed. `compute_proposed_split` returns `None`.
2. The chunk header carries `proposed_split = None`.
3. `validate_chunk_with_chunk_extra_and_receipts_root` executes the check:

```rust
if prev_chunk_extra.proposed_split() != chunk_header.proposed_split() {
    DYNAMIC_RESHARDING_VALIDATION_FAILURES.with_label_values(&["chunk_header"]).inc();
    return Err(Error::InvalidChunkHeaderShardSplit(format!(
        "header has {:?}, expected {:?} ...",
        chunk_header.proposed_split(),
        prev_chunk_extra.proposed_split(),
    )));
}
``` [3](#0-2) 

`prev_chunk_extra.proposed_split()` is `Some(parent's TrieSplit)` while `chunk_header.proposed_split()` is `None`. The check fails unconditionally for every first chunk of every child shard.

The codebase's own architecture documentation acknowledges this exact defect:

> **`chain/chain/src/resharding/manager.rs:249`** — The resharding manager doesn't set all `ChunkExtra` fields (notably the new `proposed_split` field). [4](#0-3) 

The `min_epochs_between_resharding > 0` constraint is described as a guard against back-to-back reshardings, but it is precisely this constraint that forces `compute_proposed_split` to return `None` for the child's first chunk — making the mismatch with the inherited non-`None` `ChunkExtra.proposed_split` inevitable. [5](#0-4) 

### Impact Explanation

Every dynamic resharding event produces two child shards whose first chunk is permanently rejected by `validate_chunk_with_chunk_extra_and_receipts_root`. The rejection path is reached inside `create_shard_update_job` in the normal block-processing pipeline:

```rust
validate_chunk_with_chunk_extra(
    self.chain_store(),
    self.epoch_manager.as_ref(),
    prev_hash,
    prev_chunk_extra.as_ref(),
    prev_chunk_height_included,
    chunk_header,
)
.map_err(|err| { ... byzantine_assert!(false); err })?;
``` [6](#0-5) 

A rejected chunk means the block referencing it cannot be accepted. Block production for the affected child shards stalls. All accounts whose IDs fall in those shards cannot execute transactions. The impact is a protocol-level liveness failure scoped to the post-resharding child shards, triggered automatically at the first epoch boundary after a dynamic resharding.

### Likelihood Explanation

The trigger is the `ProtocolFeature::DynamicResharding` flag. Once that feature is enabled on mainnet and the first shard exceeds the memory threshold, the resharding fires and the bug manifests deterministically on the very next epoch boundary — no adversarial input is required. Any node that tracks either child shard will independently compute the same mismatch and reject the chunk. The `proposed_split` field was added in `ChunkExtraV5` alongside the dynamic resharding feature; the clone-without-reset pattern predates the field and was never updated.

### Recommendation

In `process_memtrie_resharding_storage_update`, after cloning the parent `ChunkExtra`, explicitly reset `proposed_split` to `None` for each child:

```rust
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
// ADD: child shards start with no pending split proposal
if let ChunkExtra::V5(ref mut v5) = child_chunk_extra {
    v5.proposed_split = None;
}
```

Alternatively, expose a `proposed_split_mut()` accessor on `ChunkExtra` (parallel to `congestion_info_mut()`) and use it here. The broader TODO at line 255 should be resolved by auditing every `ChunkExtraV5` field — `outcome_root`, `validator_proposals`, `gas_used`, `gas_limit`, `balance_burnt`, `bandwidth_requests` — to confirm each is either correctly inherited from the parent or explicitly reset to a child-appropriate value. [7](#0-6) 

### Proof of Concept

1. Enable `ProtocolFeature::DynamicResharding` (protocol version gating).
2. Configure `DynamicReshardingConfig` with a low `memory_usage_threshold` so a shard crosses it within a test epoch.
3. Run the network through the resharding epoch boundary. `process_memtrie_resharding_storage_update` commits child `ChunkExtra` records with `proposed_split = Some(parent's TrieSplit)`.
4. In the next epoch, the chunk producer for a child shard calls `compute_proposed_split`; `can_reshard` returns `false` (cooldown); the produced chunk header carries `proposed_split = None`.
5. `validate_chunk_with_chunk_extra_and_receipts_root` compares `None` (header) against `Some(TrieSplit{...})` (child `ChunkExtra`) and returns `Err(InvalidChunkHeaderShardSplit(...))`.
6. The block referencing that chunk is rejected; the child shard stalls.

The divergent values are exact: `chunk_header.proposed_split() = None` vs `prev_chunk_extra.proposed_split() = Some(TrieSplit { boundary_account: <parent's boundary>, left_memory: <u64>, right_memory: <u64> })`. [7](#0-6) [3](#0-2) [2](#0-1)

### Citations

**File:** chain/chain/src/resharding/manager.rs (L255-266)
```rust
            // TODO(resharding): set all fields of `ChunkExtra`. Consider stronger
            // typing. Clarify where it should happen when `State` and
            // `FlatState` update is implemented.
            let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
            *child_chunk_extra.state_root_mut() = trie_changes.new_root;
            *child_chunk_extra.congestion_info_mut() = child_congestion_info;

            chain_store_update.save_chunk_extra(
                block_hash,
                &new_shard_uid,
                child_chunk_extra.into(),
            );
```

**File:** core/primitives/src/types.rs (L879-900)
```rust
    /// V4 -> V5: add proposed_split (dynamic resharding)
    #[derive(Debug, PartialEq, BorshSerialize, BorshDeserialize, Clone, Eq, serde::Serialize)]
    pub struct ChunkExtraV5 {
        /// Post state root after applying give chunk.
        pub state_root: StateRoot,
        /// Root of merklizing results of receipts (transactions) execution.
        pub outcome_root: CryptoHash,
        /// Validator proposals produced by given chunk.
        pub validator_proposals: Vec<ValidatorStake>,
        /// Actually how much gas were used.
        pub gas_used: Gas,
        /// Gas limit, allows to increase or decrease limit based on expected time vs real time for computing the chunk.
        pub gas_limit: Gas,
        /// Total balance burnt after processing the current chunk.
        pub balance_burnt: Balance,
        /// Congestion info about this shard after the chunk was applied.
        congestion_info: CongestionInfo,
        /// Requests for bandwidth to send receipts to other shards.
        pub bandwidth_requests: BandwidthRequests,
        /// Proposed split of this shard (dynamic resharding).
        pub proposed_split: Option<TrieSplit>,
    }
```

**File:** chain/chain/src/validate.rs (L176-185)
```rust
    if prev_chunk_extra.proposed_split() != chunk_header.proposed_split() {
        DYNAMIC_RESHARDING_VALIDATION_FAILURES.with_label_values(&["chunk_header"]).inc();
        return Err(Error::InvalidChunkHeaderShardSplit(format!(
            "header has {:?}, expected {:?} (prev block hash: {:?} height created: {:?})",
            chunk_header.proposed_split(),
            prev_chunk_extra.proposed_split(),
            chunk_header.prev_block_hash(),
            chunk_header.height_created(),
        )));
    }
```

**File:** docs/architecture/how/dynamic_resharding.md (L98-98)
```markdown
   - Checks the resharding cooldown (`can_reshard()` -- verifies `epoch_height - last_resharding >= min_epochs_between_resharding`). `min_epochs_between_resharding` must be `> 0`: allowing back-to-back reshardings is unsafe because a freshly-created child shard would inherit `proposed_split` from the parent's final chunk while its own first chunk freshly computes `proposed_split = None`, triggering `InvalidChunkHeaderShardSplit`.
```

**File:** docs/architecture/how/dynamic_resharding.md (L282-282)
```markdown
10. **`chain/chain/src/resharding/manager.rs:249`** -- The resharding manager doesn't set all `ChunkExtra` fields (notably the new `proposed_split` field).
```

**File:** chain/chain/src/chain.rs (L3455-3477)
```rust
            validate_chunk_with_chunk_extra(
                // It's safe here to use ChainStore instead of ChainStoreUpdate
                // because we're asking prev_chunk_header for already committed block
                self.chain_store(),
                self.epoch_manager.as_ref(),
                prev_hash,
                prev_chunk_extra.as_ref(),
                prev_chunk_height_included,
                chunk_header,
            )
            .map_err(|err| {
                tracing::warn!(
                    target: "chain",
                    ?err,
                    %shard_id,
                    prev_chunk_height_included,
                    ?prev_chunk_extra,
                    ?chunk_header,
                    "failed to validate chunk extra"
                );
                byzantine_assert!(false);
                err
            })?;
```

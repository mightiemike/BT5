### Title
Child `ChunkExtra.proposed_split` Inherits Parent's Non-None Value During Resharding, Causing `InvalidChunkHeaderShardSplit` on Child Shard's First Chunk — (File: chain/chain/src/resharding/manager.rs)

---

### Summary

During dynamic resharding, `process_memtrie_resharding_storage_update` creates each child shard's `ChunkExtra` by cloning the parent's `ChunkExtra` and only patching `state_root` and `congestion_info`. The `proposed_split` field — which carries the `TrieSplit` value that *triggered* the resharding — is never reset to `None`. Because the parent's last chunk computed `proposed_split = Some(TrieSplit)`, both child `ChunkExtra` records inherit that value. The child shard's first chunk producer then freshly computes `proposed_split = None` (the child is a brand-new shard, not near an epoch boundary). The chunk-header validation gate `validate_chunk_with_chunk_extra_and_receipts_root` compares `prev_chunk_extra.proposed_split()` against `chunk_header.proposed_split()` and returns `InvalidChunkHeaderShardSplit` on mismatch, halting the child shard.

---

### Finding Description

**Root cause — `chain/chain/src/resharding/manager.rs`, lines 258–260:**

```rust
// TODO(resharding): set all fields of `ChunkExtra`. Consider stronger
// typing. Clarify where it should happen when `State` and
// `FlatState` update is implemented.
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
// ← proposed_split is NOT reset; it is silently inherited from parent
``` [1](#0-0) 

The parent shard's `ChunkExtra` at the resharding block (last block of epoch N) holds `proposed_split = Some(TrieSplit{...})` — the exact split that caused the epoch manager to schedule the resharding. Both child `ChunkExtra` records are written to the DB with this inherited value.

**Validation gate — `chain/chain/src/validate.rs`, lines 176–185:**

```rust
if prev_chunk_extra.proposed_split() != chunk_header.proposed_split() {
    DYNAMIC_RESHARDING_VALIDATION_FAILURES.with_label_values(&["chunk_header"]).inc();
    return Err(Error::InvalidChunkHeaderShardSplit(format!(
        "header has {:?}, expected {:?} ...",
        chunk_header.proposed_split(),
        prev_chunk_extra.proposed_split(),
    )));
}
``` [2](#0-1) 

When the child shard's first chunk is produced (first block of epoch N+1), the chunk producer calls `compute_proposed_split`. The child shard is brand-new and not near an epoch boundary, so it returns `None`. The chunk header is signed and broadcast with `proposed_split = None`. Every validator then calls `validate_chunk_with_chunk_extra` → `validate_chunk_with_chunk_extra_and_receipts_root`, which reads the stored child `ChunkExtra` (`proposed_split = Some(TrieSplit{...})`) and compares it against the header (`proposed_split = None`). The comparison fails deterministically.

**`ChunkExtraV5` definition — `core/primitives/src/types.rs`, lines 879–900:**

```rust
/// V4 -> V5: add proposed_split (dynamic resharding)
pub struct ChunkExtraV5 {
    ...
    /// Proposed split of this shard (dynamic resharding).
    pub proposed_split: Option<TrieSplit>,
}
``` [3](#0-2) 

**The developers' own documentation acknowledges the unset field at `docs/architecture/how/dynamic_resharding.md`, line 282:**

> `chain/chain/src/resharding/manager.rs:249` — The resharding manager doesn't set all `ChunkExtra` fields (notably the new `proposed_split` field). [4](#0-3) 

The same document at line 98 notes the symptom but frames it only as a reason to require `min_epochs_between_resharding > 0`:

> a freshly-created child shard would inherit `proposed_split` from the parent's final chunk while its own first chunk freshly computes `proposed_split = None`, triggering `InvalidChunkHeaderShardSplit`. [5](#0-4) 

The cooldown is a partial mitigation (it prevents a child from being immediately re-split), but it does **not** prevent the mismatch on the child's very first chunk, which occurs unconditionally whenever the parent's `proposed_split` was non-None at the resharding block.

---

### Impact Explanation

Every validator independently runs `validate_chunk_with_chunk_extra_and_receipts_root` on the child shard's first chunk. Because the mismatch is deterministic (stored `ChunkExtra` vs. freshly computed chunk header), **all** validators reject the chunk with `InvalidChunkHeaderShardSplit`. No chunk for the child shard can be accepted. The child shard is effectively dead from the first block of the post-resharding epoch. Since dynamic resharding is triggered automatically by memory thresholds (no privileged operator action required), any shard that crosses the threshold and is split will produce this failure.

**Severity: High** — protocol-level liveness failure for the child shard, triggered automatically by the protocol upgrade boundary.

---

### Likelihood Explanation

The condition is met whenever:
1. `ProtocolFeature::DynamicResharding` is enabled (nightly now; scheduled for stabilization).
2. A shard's memory usage crosses `memory_usage_threshold` (or a `force_split_shards` entry is present).
3. The parent shard's last chunk computes `proposed_split = Some(TrieSplit)` — which is exactly the condition that causes the resharding to be scheduled.

All three conditions are satisfied by design on every dynamic resharding event. The bug fires on the very first chunk of every child shard produced by dynamic resharding.

---

### Recommendation

In `process_memtrie_resharding_storage_update`, after cloning the parent `ChunkExtra`, explicitly reset `proposed_split` to `None` for each child:

```rust
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
// Add: reset proposed_split — the child shard starts fresh with no pending split
if let ChunkExtra::V5(ref mut v5) = child_chunk_extra {
    v5.proposed_split = None;
}
```

Alternatively, expose a `proposed_split_mut()` accessor on `ChunkExtra` (analogous to `state_root_mut()`) and use it here. This also resolves the existing TODO comment at line 255. [6](#0-5) 

---

### Proof of Concept

1. Enable `ProtocolFeature::DynamicResharding` (nightly build or set `force_split_shards` in `DynamicReshardingConfig`).
2. Run a network until a shard is selected for splitting (or use `force_split_shards`).
3. At the resharding block (last block of epoch N), inspect the parent shard's `ChunkExtra`: `proposed_split` is `Some(TrieSplit{...})`.
4. Inspect the child shards' `ChunkExtra` written by `process_memtrie_resharding_storage_update`: both inherit `proposed_split = Some(TrieSplit{...})`.
5. Observe the first block of epoch N+1: the child shard's chunk producer signs a header with `proposed_split = None`.
6. All validators call `validate_chunk_with_chunk_extra_and_receipts_root` and return `InvalidChunkHeaderShardSplit` — the child shard's first chunk is universally rejected. [7](#0-6) [8](#0-7)

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

**File:** chain/chain/src/validate.rs (L132-188)
```rust
/// Validate that all next chunk information matches previous chunk extra.
pub fn validate_chunk_with_chunk_extra_and_receipts_root(
    prev_chunk_extra: &ChunkExtra,
    chunk_header: &ShardChunkHeader,
    outgoing_receipts_root: &CryptoHash,
) -> Result<(), Error> {
    if *prev_chunk_extra.state_root() != chunk_header.prev_state_root() {
        return Err(Error::InvalidStateRoot);
    }

    if prev_chunk_extra.outcome_root() != chunk_header.prev_outcome_root() {
        return Err(Error::InvalidOutcomesProof);
    }

    let chunk_extra_proposals = prev_chunk_extra.validator_proposals();
    let chunk_header_proposals = chunk_header.prev_validator_proposals();
    if chunk_header_proposals.len() != chunk_extra_proposals.len()
        || !chunk_extra_proposals.eq(chunk_header_proposals)
    {
        return Err(Error::InvalidValidatorProposals);
    }

    if prev_chunk_extra.gas_limit() != chunk_header.gas_limit() {
        return Err(Error::InvalidGasLimit);
    }

    if prev_chunk_extra.gas_used() != chunk_header.prev_gas_used() {
        return Err(Error::InvalidGasUsed);
    }

    if prev_chunk_extra.balance_burnt() != chunk_header.prev_balance_burnt() {
        return Err(Error::InvalidBalanceBurnt);
    }

    if outgoing_receipts_root != chunk_header.prev_outgoing_receipts_root() {
        return Err(Error::InvalidReceiptsProof);
    }

    validate_congestion_info(prev_chunk_extra.congestion_info(), chunk_header.congestion_info())?;
    validate_bandwidth_requests(
        prev_chunk_extra.bandwidth_requests(),
        chunk_header.bandwidth_requests(),
    )?;

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

    Ok(())
}
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

**File:** docs/architecture/how/dynamic_resharding.md (L98-98)
```markdown
   - Checks the resharding cooldown (`can_reshard()` -- verifies `epoch_height - last_resharding >= min_epochs_between_resharding`). `min_epochs_between_resharding` must be `> 0`: allowing back-to-back reshardings is unsafe because a freshly-created child shard would inherit `proposed_split` from the parent's final chunk while its own first chunk freshly computes `proposed_split = None`, triggering `InvalidChunkHeaderShardSplit`.
```

**File:** docs/architecture/how/dynamic_resharding.md (L282-282)
```markdown
10. **`chain/chain/src/resharding/manager.rs:249`** -- The resharding manager doesn't set all `ChunkExtra` fields (notably the new `proposed_split` field).
```

**File:** core/primitives/src/sharding/shard_chunk_header_inner.rs (L395-427)
```rust
// V4 -> V5: Add proposed split.
#[derive(BorshSerialize, BorshDeserialize, Clone, PartialEq, Eq, Debug, ProtocolSchema)]
pub struct ShardChunkHeaderInnerV5 {
    /// Previous block hash.
    pub prev_block_hash: CryptoHash,
    pub prev_state_root: StateRoot,
    /// Root of the outcomes from execution transactions and results of the previous chunk.
    pub prev_outcome_root: CryptoHash,
    pub encoded_merkle_root: CryptoHash,
    pub encoded_length: u64,
    pub height_created: BlockHeight,
    /// Shard index.
    pub shard_id: ShardId,
    /// Gas used in the previous chunk.
    pub prev_gas_used: Gas,
    /// Gas limit voted by validators.
    pub gas_limit: Gas,
    /// Total balance burnt in the previous chunk.
    pub prev_balance_burnt: Balance,
    /// Previous chunk's outgoing receipts merkle root.
    pub prev_outgoing_receipts_root: CryptoHash,
    /// Tx merkle root.
    pub tx_root: CryptoHash,
    /// Validator proposals from the previous chunk.
    pub prev_validator_proposals: Vec<ValidatorStake>,
    /// Congestion info about this shard after the previous chunk was applied.
    pub congestion_info: CongestionInfo,
    /// Requests for bandwidth to send receipts to other shards.
    pub bandwidth_requests: BandwidthRequests,
    /// Proposed split of this shard (dynamic resharding).
    /// `None` if the shard is not above the resharding threshold.
    pub proposed_split: Option<TrieSplit>,
}
```

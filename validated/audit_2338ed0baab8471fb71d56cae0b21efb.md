### Title
Child `ChunkExtra.proposed_split` Inherits Stale Parent Value at Resharding Boundary, Causing `InvalidChunkHeaderShardSplit` - (File: chain/chain/src/resharding/manager.rs)

### Summary

During shard splitting under `DynamicResharding` (protocol version 85), `process_memtrie_resharding_storage_update()` creates each child shard's `ChunkExtra` by cloning the parent's `ChunkExtra` and only overwriting `state_root` and `congestion_info`. The `proposed_split` field — which the parent's final chunk may carry as `Some(TrieSplit{...})` because it was the chunk that triggered the split — is never reset to `None`. The child shard's first chunk producer then computes `proposed_split = None` (the resharding cooldown blocks immediate re-splitting), producing a mismatch that `validate_chunk_with_chunk_extra_and_receipts_root()` rejects as `InvalidChunkHeaderShardSplit`. The child shard's first chunk is permanently unacceptable to validators.

### Finding Description

In `process_memtrie_resharding_storage_update()`:

```rust
// TODO(resharding): set all fields of `ChunkExtra`. Consider stronger
// typing. Clarify where it should happen when `State` and
// `FlatState` update is implemented.
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
``` [1](#0-0) 

Only `state_root` and `congestion_info` are updated. `proposed_split` is silently inherited from the parent.

`ChunkExtraV5` carries `proposed_split: Option<TrieSplit>`: [2](#0-1) 

The parent shard's last chunk — the one that proposed the split — has `proposed_split = Some(TrieSplit{boundary_account, left_memory, right_memory})`. This is the exact chunk whose `ChunkExtra` is read at line 186–187 and then cloned into the child: [3](#0-2) 

The child shard's first chunk producer calls `compute_proposed_split()`, which returns `None` because `can_reshard()` returns `false` (the resharding cooldown enforced by `min_epochs_between_resharding > 0` has not elapsed): [4](#0-3) 

Chunk validation then compares the stale `ChunkExtra.proposed_split = Some(...)` against the freshly-computed `chunk_header.proposed_split = None`: [5](#0-4) 

The mismatch is fatal: `InvalidChunkHeaderShardSplit` is returned and the chunk is rejected. Because `compute_proposed_split()` is deterministic given the same trie state, every subsequent attempt by the child shard's chunk producer produces the same `None`, and the child shard is permanently stuck.

The codebase's own architecture document acknowledges the symptom but misattributes the mitigation:

> `min_epochs_between_resharding` must be `> 0`: allowing back-to-back reshardings is unsafe because a freshly-created child shard would inherit `proposed_split` from the parent's final chunk while its own first chunk freshly computes `proposed_split = None`, triggering `InvalidChunkHeaderShardSplit`.

The cooldown only prevents the child from *proposing* a new split; it does not prevent the child's `ChunkExtra` from carrying the parent's stale `proposed_split`, which is the actual root cause.

### Impact Explanation

When `DynamicResharding` (protocol version 85) is active and a shard split occurs, the child shards' first chunks are rejected by all validators. No valid chunk can be produced for the child shards because the divergent Borsh-serialized `proposed_split` field in the stored `ChunkExtraV5` is irreconcilable with the deterministically-computed `None` in every subsequent chunk header. The child shards are effectively halted from the first block of the post-resharding epoch, breaking liveness for all accounts assigned to those shards.

### Likelihood Explanation

The trigger condition is: the parent shard's last chunk (the one that caused the split to be scheduled) must have `proposed_split = Some(...)`. By definition, this is always true — the split is scheduled precisely because a chunk proposed it. Therefore, every dynamic resharding event that reaches the child-shard creation step will exhibit this bug. The feature is stable at protocol version 85 and the epoch config for mainnet at that version exists in the repository. [6](#0-5) 

### Recommendation

In `process_memtrie_resharding_storage_update()`, after cloning the parent `ChunkExtra` and before saving it, explicitly reset `proposed_split` to `None` for each child shard:

```rust
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
// Reset proposed_split: the child shard starts fresh and has not
// evaluated its own trie for a split yet.
if let ChunkExtra::V5(ref mut v5) = child_chunk_extra {
    v5.proposed_split = None;
}
```

Alternatively, expose a `proposed_split_mut()` accessor on `ChunkExtra` (analogous to `congestion_info_mut()`) and use it here, which would also resolve the broader TODO about setting all fields.

### Proof of Concept

1. Enable `DynamicResharding` (protocol version 85, already stable).
2. Configure `force_split_shards` in `DynamicReshardingConfig` to force a split of a specific shard at the next epoch boundary.
3. Run the network for one epoch so the forced shard's last chunk records `proposed_split = Some(TrieSplit{...})` in its `ChunkExtra`.
4. At the epoch boundary, `process_memtrie_resharding_storage_update()` clones that `ChunkExtra` into both child shards, preserving `proposed_split = Some(...)`.
5. In the next epoch, the child shard's chunk producer calls `compute_proposed_split()`, which returns `None` (cooldown not elapsed).
6. `validate_chunk_with_chunk_extra_and_receipts_root()` compares `prev_chunk_extra.proposed_split() = Some(...)` against `chunk_header.proposed_split() = None` and returns `Err(Error::InvalidChunkHeaderShardSplit(...))`.
7. The child shard produces no accepted chunks; all accounts on the child shard are unreachable. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** chain/chain/src/resharding/manager.rs (L186-188)
```rust
        let parent_chunk_extra =
            self.store.chunk_store().get_chunk_extra(block_hash, parent_shard_uid)?;
        let mut store_update = self.store.trie_store().store_update();
```

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

**File:** core/primitives/src/types.rs (L1066-1071)
```rust
        pub fn proposed_split(&self) -> Option<&TrieSplit> {
            match self {
                Self::V1(_) | Self::V2(_) | Self::V3(_) | Self::V4(_) => None,
                ChunkExtra::V5(v5) => v5.proposed_split.as_ref(),
            }
        }
```

**File:** chain/chain/src/runtime/mod.rs (L591-605)
```rust
        if !ProtocolFeature::DynamicResharding.enabled(protocol_version) {
            return Ok(None);
        }

        let Some(config) = epoch_config.dynamic_resharding_config() else {
            return Ok(None);
        };

        if !self.epoch_manager.is_next_block_possibly_last_in_epoch(height, prev_block_hash)? {
            return Ok(None);
        }

        if !self.epoch_manager.can_reshard(prev_block_hash, config.min_epochs_between_resharding)? {
            return Ok(None);
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

**File:** core/primitives/res/epoch_configs/mainnet/85.json (L1-44)
```json
{
  "epoch_length": 43200,
  "num_block_producer_seats": 100,
"block_producer_kickout_threshold": 80,
  "chunk_producer_kickout_threshold": 80,
  "chunk_validator_only_kickout_threshold": 70,
  "target_validator_mandates_per_shard": 105,
  "validator_max_kickout_stake_perc": 30,
  "online_min_threshold": [
    90,
    100
  ],
  "online_max_threshold": [
    99,
    100
  ],
  "fishermen_threshold": "340282366920938463463374607431768211455",
  "minimum_stake_divisor": 10,
  "protocol_upgrade_stake_threshold": [
    4,
    5
  ],
  "dynamic_resharding_config": {
    "memory_usage_threshold": 40000000000,
    "min_child_memory_usage": 10000000000,
    "max_number_of_shards": 10,
    "min_epochs_between_resharding": 2,
    "force_split_shards": [],
    "block_split_shards": []
  },
  "num_chunk_producer_seats": 100,
  "num_chunk_validator_seats": 500,
"minimum_validators_per_shard": 1,
  "minimum_stake_ratio": [
    1,
    62500
  ],
  "chunk_producer_assignment_changes_limit": 5,
  "shuffle_shard_assignment_for_chunk_producers": false,
  "max_inflation_rate": [
    1,
    40
  ]
}
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

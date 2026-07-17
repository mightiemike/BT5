### Title
Child Shard `ChunkExtra` Inherits Parent's Non-Null `proposed_split` Causing `InvalidChunkHeaderShardSplit` After Dynamic Resharding — (`File: chain/chain/src/resharding/manager.rs`)

### Summary

When a shard is dynamically split, the child shard's initial `ChunkExtra` is cloned from the parent's `ChunkExtra` without resetting the `proposed_split` field to `None`. The child's first chunk producer independently recomputes `proposed_split = None` (because the epoch just started). The chunk-header validation then compares the inherited non-null `proposed_split` in `prev_chunk_extra` against the freshly-computed `None` in the chunk header and rejects the chunk with `InvalidChunkHeaderShardSplit`, stalling the child shard.

### Finding Description

**Two independent channels produce divergent `proposed_split` values for the child shard's first chunk:**

**Channel 1 — State inheritance (resharding path):**
In `process_memtrie_resharding_storage_update`, the child `ChunkExtra` is created by cloning the parent's `ChunkExtra` and updating only `state_root` and `congestion_info`:

```rust
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
```

The `proposed_split` field is **not reset**. If the parent shard was above the memory threshold near the epoch boundary, its final `ChunkExtra` carries `proposed_split = Some(boundary_account)`, and the child inherits this value verbatim. [1](#0-0) 

**Channel 2 — Fresh computation (chunk production path):**
When the child shard's first chunk producer calls `compute_proposed_split` at the start of epoch N+2, `is_next_block_possibly_last_in_epoch` returns `false` (the epoch just started), so `compute_proposed_split` returns `None`. The chunk header is therefore produced with `proposed_split = None`. [2](#0-1) 

**Validation detects the mismatch and rejects the chunk:**
`validate_chunk_with_chunk_extra_and_receipts_root` compares `prev_chunk_extra.proposed_split()` (inherited `Some(boundary_account)`) against `chunk_header.proposed_split()` (`None`) and returns `InvalidChunkHeaderShardSplit`:

```rust
if prev_chunk_extra.proposed_split() != chunk_header.proposed_split() {
    return Err(Error::InvalidChunkHeaderShardSplit(...));
}
``` [3](#0-2) 

The `proposed_split` field exists only in `ChunkExtraV5` and `ShardChunkHeaderInnerV5`, both introduced with `ProtocolFeature::DynamicResharding` (protocol version 85, enabled in the current stable binary at version 86). [4](#0-3) [5](#0-4) 

The dynamic resharding documentation itself acknowledges the back-to-back resharding hazard and states that `min_epochs_between_resharding > 0` is required to prevent it, but the cooldown only prevents a *second* resharding from being scheduled too soon — it does not prevent the parent's non-null `proposed_split` from being inherited by the child at the resharding boundary. [6](#0-5) 

### Impact Explanation

The child shard's first chunk is unconditionally rejected by every validating node. No honest chunk producer can produce a valid first chunk for the child shard because the `prev_chunk_extra` stored on disk carries `proposed_split = Some(...)` while the protocol requires the chunk header to carry the freshly-computed `None`. The child shard stalls at the first block of epoch N+2, halting all transactions whose accounts fall in the child shard's range. Because the mismatch is deterministic and affects all nodes equally, there is no recovery path without a protocol-level fix or a coordinated rollback.

### Likelihood Explanation

`DynamicResharding` is enabled at protocol version 85 and the current stable binary runs at version 86, so the code path is live on mainnet. The trigger condition — parent shard above the memory threshold near epoch end — is precisely the condition that causes a resharding to be proposed in the first place. Any successful dynamic resharding where the parent shard's memory usage exceeds `memory_usage_threshold` during the last few blocks of epoch N will produce a non-null `proposed_split` in the parent's final `ChunkExtra`, which is then inherited by the child. The bug fires on the very first block the child shard must produce.

### Recommendation

In `process_memtrie_resharding_storage_update`, explicitly reset `proposed_split` to `None` on the child `ChunkExtra` after cloning from the parent:

```rust
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
// Reset proposed_split: the child starts a new epoch and must recompute
// its own split proposal; inheriting the parent's value causes
// InvalidChunkHeaderShardSplit on the child's first chunk.
*child_chunk_extra.proposed_split_mut() = None;
```

A corresponding `proposed_split_mut()` accessor must be added to `ChunkExtra` (mirroring the existing `state_root_mut()` and `congestion_info_mut()` pattern). A regression test should verify that after a dynamic resharding event where the parent's final `ChunkExtra` carries a non-null `proposed_split`, the child shard's first chunk is accepted without error. [7](#0-6) 

### Proof of Concept

1. Enable `DynamicResharding` (protocol version ≥ 85, already stable).
2. Configure a shard with `memory_usage_threshold` low enough that it triggers a split proposal near the epoch boundary.
3. Allow the resharding to proceed: at the last block of epoch N, the parent shard's `ChunkExtra` will have `proposed_split = Some(boundary_account)`.
4. At the start of epoch N+2, the child shard's chunk producer calls `compute_proposed_split`; `is_next_block_possibly_last_in_epoch` returns `false` → `proposed_split = None` → chunk header carries `None`.
5. `validate_chunk_with_chunk_extra_and_receipts_root` reads `prev_chunk_extra.proposed_split() = Some(boundary_account)` and `chunk_header.proposed_split() = None`; the comparison fails and returns `Err(Error::InvalidChunkHeaderShardSplit(...))`.
6. The child shard's first chunk is rejected by all validators; the shard stalls. [8](#0-7) [9](#0-8) [10](#0-9) [11](#0-10) [12](#0-11)

### Citations

**File:** chain/chain/src/resharding/manager.rs (L258-295)
```rust
            let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
            *child_chunk_extra.state_root_mut() = trie_changes.new_root;
            *child_chunk_extra.congestion_info_mut() = child_congestion_info;

            chain_store_update.save_chunk_extra(
                block_hash,
                &new_shard_uid,
                child_chunk_extra.into(),
            );
            chain_store_update.save_state_transition_data(
                *block_hash,
                new_shard_uid.shard_id(),
                parent_trie.recorded_storage(),
                CryptoHash::default(),
                // No contract code is accessed or deployed during resharding.
                // TODO(#11099): Confirm if sending no contracts is ok here.
                Default::default(),
            );

            tracing::info!(target: "resharding", ?new_shard_uid, ?trie_changes.new_root, "child trie created");

            split_shard_trie_changes.trie_changes.insert(*new_shard_uid, trie_changes);
        }

        // After committing the split changes, the parent trie has the state
        // root of both the children. Now we can freeze the parent memtrie and
        // copy it to the children.
        let parent_trie =
            tries.get_trie_for_shard(*parent_shard_uid, *parent_chunk_extra.state_root());
        if parent_trie.has_memtries() {
            tries.freeze_parent_memtrie(*parent_shard_uid, split_shard_event.children_shards())?;
        }

        chain_store_update.merge(store_update.into());
        chain_store_update.commit()?;

        Ok(split_shard_trie_changes)
    }
```

**File:** chain/chain/src/runtime/mod.rs (L581-620)
```rust
    fn compute_proposed_split(
        &self,
        shard_trie: &Trie,
        shard_id: ShardId,
        epoch_id: &EpochId,
        protocol_version: ProtocolVersion,
        epoch_config: &EpochConfig,
        height: BlockHeight,
        prev_block_hash: &CryptoHash,
    ) -> Result<Option<TrieSplit>, Error> {
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

        let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;
        let shard_uid = ShardUId::from_shard_id_and_layout(shard_id, &shard_layout);
        match check_dynamic_resharding(shard_trie, shard_id, shard_layout, config) {
            Err(FindSplitError::Storage(err)) => Err(err)?,
            Err(err) => {
                DYNAMIC_RESHARDING_FIND_SPLIT_ERRORS
                    .with_label_values(&[&shard_uid.to_string()])
                    .inc();
                tracing::error!(target: "runtime", ?shard_id, ?err, "dynamic resharding check failed");
                Ok(None)
            }
            Ok(split) => Ok(split),
        }
    }
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

**File:** core/primitives/src/epoch_info.rs (L227-246)
```rust
        if ProtocolFeature::DynamicResharding.enabled(protocol_version) {
            return Self::V5(EpochInfoV5 {
                epoch_height,
                validators,
                validator_to_index,
                block_producers_settlement,
                chunk_producers_settlement,
                stake_change,
                validator_reward,
                validator_kickout,
                minted_amount,
                seat_price,
                protocol_version,
                shard_layout,
                last_resharding,
                rng_seed,
                block_producers_sampler,
                chunk_producers_sampler,
                validator_mandates,
            });
```

**File:** core/primitives-core/src/version.rs (L555-571)
```rust
            ProtocolFeature::FixDelegateActionDepositWithFunctionCallError
            | ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
            | ProtocolFeature::FixDelegatedDeterministicStateInit
            | ProtocolFeature::GasKeys
            | ProtocolFeature::ContinuousEpochSync
            | ProtocolFeature::DynamicResharding
            | ProtocolFeature::StickyReshardingValidatorAssignment
            | ProtocolFeature::StrictNonce
            | ProtocolFeature::PostQuantumSignatures
            | ProtocolFeature::UniqueChunkTransactions
            | ProtocolFeature::ValidateBlockOrdinalAndEpochSyncDataHash
            | ProtocolFeature::YieldWithId
            | ProtocolFeature::ExecutionMetadataV4
            | ProtocolFeature::SignedContractCodeResponse
            | ProtocolFeature::ClampOutgoingGasAdmission
            | ProtocolFeature::AccountCostIncrease
            | ProtocolFeature::DelegateV2 => 85,
```

**File:** docs/architecture/how/dynamic_resharding.md (L96-99)
```markdown
2. Calls `get_upcoming_shard_split()` which:
   - Checks if dynamic resharding is enabled (via `ShardLayoutConfig::Dynamic`).
   - Checks the resharding cooldown (`can_reshard()` -- verifies `epoch_height - last_resharding >= min_epochs_between_resharding`). `min_epochs_between_resharding` must be `> 0`: allowing back-to-back reshardings is unsafe because a freshly-created child shard would inherit `proposed_split` from the parent's final chunk while its own first chunk freshly computes `proposed_split = None`, triggering `InvalidChunkHeaderShardSplit`.
   - Calls `pick_shard_to_split()` to select the winning shard: forced shards have priority, otherwise the shard with highest `total_memory()` wins.
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

### Title
Dynamic Resharding Proposals Silently Suppressed When Spice Feature Is Active Due to Missing `proposed_split` in `ShardChunkHeaderInnerV6` — (`core/primitives/src/sharding/shard_chunk_header_inner.rs`)

---

### Summary

When the `Spice` protocol feature is active, chunk headers use the `ShardChunkHeaderInnerV6SpiceTxOnly` format, which has no `proposed_split` field. The `proposed_split()` accessor for `V6` unconditionally returns `None` with an explicit `TODO` comment. The epoch-manager's `get_upcoming_shard_split()` collects split proposals exclusively by calling `chunk_header.proposed_split()` over all chunk headers. Because every `V6` chunk silently returns `None`, no split proposal is ever collected, `shard_split` in the block header is always `None`, and `next_next_shard_layout()` never derives a new layout. Dynamic resharding is permanently and silently suppressed for the entire lifetime of a Spice-enabled chain.

---

### Finding Description

**Step 1 — The missing field.**

`ShardChunkHeaderInnerV6SpiceTxOnly` is the chunk-header inner type introduced for Spice. It contains only `prev_block_hash`, `encoded_merkle_root`, `encoded_length`, `height_created`, `shard_id`, `prev_outgoing_receipts_root`, and `tx_root`. There is no `proposed_split` field. [1](#0-0) 

**Step 2 — The accessor always returns `None` for `V6`.**

The `proposed_split()` method on `ShardChunkHeaderInner` explicitly returns `None` for `V6` with a `TODO(spice)` comment acknowledging the gap:

```rust
Self::V6(_) => {
    // TODO(spice): pass shard split to produce_block in another way
    None
}
``` [2](#0-1) 

**Step 3 — `get_upcoming_shard_split()` relies entirely on `proposed_split()` from chunk headers.**

At the last block of each epoch, the block producer calls `get_upcoming_shard_split()`, which iterates over all chunk headers and collects split proposals:

```rust
for chunk_header in chunk_headers {
    if let Some(split) = chunk_header.proposed_split() {
        proposed_splits.insert(chunk_header.shard_id(), split.clone());
    }
}
```

When all chunk headers are `V6`, `proposed_splits` is always empty, `pick_shard_to_split` finds nothing, and the function returns `Ok(None)`. [3](#0-2) 

**Step 4 — `shard_split` in the block header is always `None`.**

In `client.rs`, `shard_split` is set to the result of `get_upcoming_shard_split()` only when the block is the last of the epoch. Since that function always returns `None` under Spice, the `shard_split` field in `BlockHeaderInnerRestV6` is always `None`. [4](#0-3) 

**Step 5 — `next_next_shard_layout()` never derives a new layout.**

`finalize_epoch()` calls `next_next_shard_layout()`, which reads `block_info.shard_split()`. Because `shard_split` is always `None`, the function falls through to `return Ok(next_shard_layout.clone())` — the layout for epoch N+2 is always identical to epoch N+1. No resharding ever occurs. [5](#0-4) 

**Step 6 — `BlockInfo::shard_split()` for `V3` (non-Spice) blocks also returns `None`.**

For completeness: `BlockInfo::V3` (the pre-dynamic-resharding format) also has no `shard_split` field, and `shard_split()` returns `None` for `V1`/`V2`/`V3`. The `V4`/`V5` variants carry the field. Under Spice, `BlockInfo::V5` is used, which does carry `shard_split` — but since the block header's `shard_split` is always `None` (Step 4), `BlockInfoV5.shard_split` is always `None` too. [6](#0-5) 

---

### Impact Explanation

Dynamic resharding (`ProtocolFeature::DynamicResharding`, protocol version 85) and Spice (`ProtocolFeature::Spice`, protocol version 180) are both enabled in nightly builds. When both are active, every chunk header is `V6`, `proposed_split()` always returns `None`, and the entire dynamic resharding pipeline is silently dead. Shards will never split regardless of memory pressure. The `EpochInfoV5.shard_layout` field is never updated, the `last_resharding` cooldown counter is never advanced, and the memtrie preload for resharding is never triggered. The network's ability to scale by splitting shards is permanently lost for the duration of the Spice-enabled chain, with no error, no log, and no metric indicating the failure.

This is a **High** severity protocol-compatibility break: a feature that was explicitly designed to be always-on (dynamic resharding fires automatically when memory thresholds are exceeded) is silently rendered inoperative by a version-boundary interaction.

---

### Likelihood Explanation

Both features share the same nightly protocol version (85 for `DynamicResharding`, 180 for `Spice`). Any nightly or betanet deployment that enables Spice will immediately suppress dynamic resharding. The `TODO(spice)` comment in the source confirms the developers are aware of the gap but have not yet resolved it. The suppression requires no attacker — it is triggered automatically by the protocol version upgrade. [7](#0-6) 

---

### Recommendation

`ShardChunkHeaderInnerV6SpiceTxOnly` must carry a `proposed_split: Option<TrieSplit>` field, or an alternative mechanism must be established to propagate the split proposal from `ChunkExtra` to the block producer without going through the chunk header. The `TODO(spice): pass shard split to produce_block in another way` comment identifies the exact location. Until this is resolved, dynamic resharding and Spice must not be simultaneously active on any chain where shard growth is expected.

---

### Proof of Concept

The invariant break is directly readable from the code path:

1. `ShardChunkHeaderInnerV6SpiceTxOnly` has no `proposed_split` field. [8](#0-7) 

2. `proposed_split()` returns `None` for `V6` (with TODO comment). [9](#0-8) 

3. `get_upcoming_shard_split()` collects proposals only via `chunk_header.proposed_split()` — empty under Spice. [10](#0-9) 

4. Block producer sets `shard_split = None` when `get_upcoming_shard_split()` returns `None`. [4](#0-3) 

5. `next_next_shard_layout()` carries forward the existing layout unchanged when `block_info.shard_split()` is `None`. [5](#0-4) 

6. `finalize_epoch()` stores the unchanged layout in `EpochInfoV5` for epoch N+2 — no resharding ever occurs. [11](#0-10)

### Citations

**File:** core/primitives/src/sharding/shard_chunk_header_inner.rs (L269-279)
```rust
    #[inline]
    pub fn proposed_split(&self) -> Option<&TrieSplit> {
        match self {
            Self::V1(_) | Self::V2(_) | Self::V3(_) | Self::V4(_) => None,
            Self::V5(inner) => inner.proposed_split.as_ref(),
            Self::V6(_) => {
                // TODO(spice): pass shard split to produce_block in another way
                None
            }
        }
    }
```

**File:** core/primitives/src/sharding/shard_chunk_header_inner.rs (L429-448)
```rust
// V5 -> V6: a version for spice of a chunk header including only transactions (no previous
// execution results).
#[derive(BorshSerialize, BorshDeserialize, Clone, PartialEq, Eq, Debug, ProtocolSchema)]
pub struct ShardChunkHeaderInnerV6SpiceTxOnly {
    /// Previous block hash.
    pub prev_block_hash: CryptoHash,
    pub encoded_merkle_root: CryptoHash,
    pub encoded_length: u64,
    pub height_created: BlockHeight,
    /// Shard index.
    pub shard_id: ShardId,
    // TODO(spice): remove prev_outgoing_receipts_root. We have it for now
    // so that some of the existing validations pass. List of outgoing receipts is always empty,
    // but it wouldn't mean that prev_outgoing_receipts_root is CryptoHash::default() since it's
    // computed as root of merkle tree of those empty lists from all shards.
    /// Previous chunk's outgoing receipts merkle root.
    pub prev_outgoing_receipts_root: CryptoHash,
    /// Tx merkle root.
    pub tx_root: CryptoHash,
}
```

**File:** chain/epoch-manager/src/lib.rs (L762-763)
```rust
        let Some((shard_id, boundary_account)) = block_info.shard_split() else {
            return Ok(next_shard_layout.clone());
```

**File:** chain/epoch-manager/src/lib.rs (L918-924)
```rust
        let next_next_shard_layout = self.next_next_shard_layout(
            &epoch_config,
            epoch_protocol_version,
            &next_next_epoch_config,
            &next_shard_layout,
            block_info,
        )?;
```

**File:** chain/epoch-manager/src/lib.rs (L2212-2228)
```rust
        // Collect proposed splits from chunk headers
        let mut proposed_splits = HashMap::new();
        for chunk_header in chunk_headers {
            if let Some(split) = chunk_header.proposed_split() {
                proposed_splits.insert(chunk_header.shard_id(), split.clone());
            }
        }

        // Pick the shard to split
        let Some((shard_id, split)) =
            pick_shard_to_split(&proposed_splits, dynamic_resharding_config)
        else {
            return Ok(None);
        };

        Ok(Some((shard_id, split.boundary_account)))
    }
```

**File:** chain/client/src/client.rs (L1128-1136)
```rust
        let shard_split = if is_produced_block_last_in_epoch {
            self.epoch_manager.get_upcoming_shard_split(
                protocol_version,
                &prev_hash,
                &chunk_headers,
            )?
        } else {
            None
        };
```

**File:** core/primitives/src/epoch_block_info.rs (L302-311)
```rust
    #[inline]
    pub fn shard_split(&self) -> Option<&(ShardId, AccountId)> {
        match self {
            Self::V1(_) => None,
            Self::V2(_) => None,
            Self::V3(_) => None,
            Self::V4(info) => info.shard_split.as_ref(),
            Self::V5(info) => info.shard_split.as_ref(),
        }
    }
```

**File:** core/primitives-core/src/version.rs (L559-571)
```rust
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

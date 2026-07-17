### Title
`can_reshard()` Cooldown Invariant Broken at Static-to-Dynamic Resharding Protocol Activation Boundary — (`chain/epoch-manager/src/lib.rs`)

### Summary

When `ProtocolFeature::DynamicResharding` activates, the first `EpochInfoV5` is created with `last_resharding: None` because no migration carries the epoch height of the most recent *static* resharding into the new schema. The `can_reshard()` cooldown check treats `None` as "no resharding ever happened" and unconditionally returns `true`, bypassing the `min_epochs_between_resharding` safety gate at the exact protocol-version boundary where it is most needed.

### Finding Description

`EpochInfo::new()` branches on `ProtocolFeature::DynamicResharding.enabled(protocol_version)` to decide whether to produce a `V4` or `V5` struct: [1](#0-0) 

`EpochInfoV5` adds `shard_layout` and `last_resharding` fields: [2](#0-1) 

`last_resharding()` returns `None` for every pre-V5 variant: [3](#0-2) 

In `finalize_epoch()`, the `last_resharding` value forwarded into the first `EpochInfoV5` is computed as: [4](#0-3) 

When `next_epoch_info` is still `V4` (the epoch immediately before activation), `next_epoch_info.last_resharding()` returns `None`. If no dynamic resharding has yet occurred, `has_same_shard_layout` is `true`, so `last_resharding` is `None`. The first `EpochInfoV5` is therefore written with `last_resharding: None` regardless of whether a static resharding completed one epoch earlier.

`can_reshard()` then reads this field: [5](#0-4) 

The comment on line 842–844 explicitly acknowledges the broken assumption: *"It is theoretically possible that a static resharding was scheduled right before enabling dynamic resharding, but we assume this didn't happen."* The `is_none_or` combinator makes `None` unconditionally pass the cooldown gate.

`can_reshard()` is called in two places:

1. `get_upcoming_shard_split()` — block production / block validation: [6](#0-5) 

2. `compute_proposed_split()` — chunk application: [7](#0-6) 

### Impact Explanation

The cooldown invariant (`min_epochs_between_resharding > 0`) is documented as a hard safety requirement:

> *"allowing back-to-back reshardings is unsafe because a freshly-created child shard would inherit `proposed_split` from the parent's final chunk while its own first chunk freshly computes `proposed_split = None`, triggering `InvalidChunkHeaderShardSplit`."* [8](#0-7) 

If a static resharding completes in epoch N and `DynamicResharding` activates in epoch N+1:

- The first `EpochInfoV5` has `last_resharding: None`.
- `can_reshard()` returns `true` in epoch N+1.
- A dynamic resharding is proposed at the end of epoch N+1 (two-epoch delay → takes effect at epoch N+3).
- The child shards from the static resharding (born at epoch N+2) are targeted for splitting at epoch N+3 — only one epoch after they were created.
- The child shard's first chunk (epoch N+2) computes `proposed_split = None`; the inherited `proposed_split` from the parent's final chunk (epoch N+1) is non-`None`. This mismatch triggers `InvalidChunkHeaderShardSplit` during chunk header validation: [9](#0-8) 

Additionally, the flat storage resharding actor panics if the parent shard's flat storage is not `Ready`: [10](#0-9) 

A child shard born at epoch N+2 whose flat storage is still being built (catch-up phase) would trigger this panic when the dynamic resharding actor attempts to split it at epoch N+3.

**Severity: High.** The consequence is `InvalidChunkHeaderShardSplit` validation failures across all validators (consensus-breaking) or a node panic in the resharding actor, both of which halt block production for the affected shard.

### Likelihood Explanation

The scenario requires two conditions to coincide:

1. A static resharding (e.g., Simple Nightshade V3 → V4) completes in the epoch immediately before `DynamicResharding` activates. This is a planned, foreseeable deployment sequence: static resharding is the prerequisite for dynamic resharding.
2. The memory threshold is exceeded in the first dynamic epoch (or `force_split_shards` is set in `DynamicReshardingConfig`). Given that static reshardings are triggered precisely because shards are large, the memory threshold being exceeded immediately after activation is realistic.

Neither condition requires any privileged action beyond the normal protocol upgrade process. The code itself flags this as an unverified assumption rather than a guaranteed invariant.

### Recommendation

In `finalize_epoch()`, when constructing the `last_resharding` value to pass into `proposals_to_epoch_info`, also check whether the *current* epoch's shard layout differs from the *previous* epoch's shard layout (i.e., a static resharding just took effect). If so, initialize `last_resharding` to the current epoch height rather than forwarding `None` from the pre-V5 `EpochInfo`:

```rust
// Before computing next_next_shard_layout, check if a static resharding
// just took effect (current epoch layout != prev epoch layout).
let static_resharding_epoch_height = {
    let prev_epoch_layout = /* layout of epoch N-1 */;
    let current_epoch_layout = /* layout of epoch N */;
    if prev_epoch_layout != current_epoch_layout {
        Some(epoch_info.epoch_height())
    } else {
        None
    }
};

let last_resharding = (!has_same_shard_layout)
    .then(|| next_epoch_info.epoch_height() + 1)
    .or_else(|| next_epoch_info.last_resharding())
    .or(static_resharding_epoch_height); // carry forward static resharding history
```

Alternatively, remove the `is_none_or` shortcut in `can_reshard()` and instead explicitly check whether the next epoch's protocol version is the first to enable `DynamicResharding`, and if so, look up the most recent static resharding epoch from the layout history.

### Proof of Concept

**Epoch sequence:**

| Epoch | Protocol Version | EpochInfo | Shard Layout | Event |
|-------|-----------------|-----------|--------------|-------|
| N | 84 (pre-dynamic) | V4 | V2 (5 shards) | Static resharding vote |
| N+1 | 85 (DynamicResharding) | **V5** (`last_resharding=None`) | V2→V3 transition | First dynamic epoch; `can_reshard()` → `true` |
| N+2 | 85 | V5 | V3 (6 shards, child shards born) | Dynamic split proposed at end of N+1 takes effect |
| N+3 | 85 | V5 | V3 split again | Child shard (1 epoch old) targeted; `InvalidChunkHeaderShardSplit` |

The divergent Borsh bytes: `EpochInfoV5.last_resharding` is serialized as `Option<EpochHeight>` — the wire value is `0x00` (None) when it should be `0x01 || epoch_height_le64` (Some(N)), causing `can_reshard()` to compute `true` instead of `false` for the first dynamic epoch. [11](#0-10) [12](#0-11)

### Citations

**File:** core/primitives/src/epoch_info.rs (L45-71)
```rust
// V4 -> V5: Add shard layout (for dynamic resharding)
#[derive(
    BorshSerialize, BorshDeserialize, Clone, Debug, PartialEq, Eq, serde::Serialize, ProtocolSchema,
)]
pub struct EpochInfoV5 {
    pub epoch_height: EpochHeight,
    pub validators: Vec<ValidatorStake>,
    pub validator_to_index: HashMap<AccountId, ValidatorId>,
    pub block_producers_settlement: Vec<ValidatorId>,
    pub chunk_producers_settlement: Vec<Vec<ValidatorId>>,
    pub stake_change: BTreeMap<AccountId, Balance>,
    pub validator_reward: HashMap<AccountId, Balance>,
    pub validator_kickout: HashMap<AccountId, ValidatorKickoutReason>,
    pub minted_amount: Balance,
    pub seat_price: Balance,
    pub protocol_version: ProtocolVersion,
    pub shard_layout: ShardLayout,
    /// The epoch height at which the most recent resharding occurred.
    /// `None` means no resharding has happened since dynamic resharding was enabled.
    pub last_resharding: Option<EpochHeight>,
    // stuff for selecting validators at each height
    rng_seed: RngSeed,
    block_producers_sampler: StakeWeightedIndex,
    chunk_producers_sampler: Vec<StakeWeightedIndex>,
    /// Contains the epoch's validator mandates. Used to sample chunk validators.
    validator_mandates: ValidatorMandates,
}
```

**File:** core/primitives/src/epoch_info.rs (L227-247)
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
        }
```

**File:** core/primitives/src/epoch_info.rs (L704-711)
```rust
    /// Get the epoch height at which the most recent resharding occurred.
    /// Returns `None` for pre-V5 `EpochInfo` or when no resharding has happened.
    pub fn last_resharding(&self) -> Option<EpochHeight> {
        match self {
            Self::V1(_) | Self::V2(_) | Self::V3(_) | Self::V4(_) => None,
            Self::V5(v5) => v5.last_resharding,
        }
    }
```

**File:** chain/epoch-manager/src/lib.rs (L833-849)
```rust
    fn can_reshard(
        &self,
        block_hash: &CryptoHash,
        min_epochs_between_resharding: NonZeroEpochHeight,
    ) -> Result<bool, EpochError> {
        let block_info = self.get_block_info(block_hash)?;
        let next_epoch_id = self.get_next_epoch_id_from_info(&block_info)?;
        let next_epoch_info = self.get_epoch_info(&next_epoch_id)?;

        // last_resharding() returns `None` if no resharding happened since dynamic resharding
        // has been enabled. It is theoretically possible that a static resharding was scheduled
        // right before enabling dynamic resharding, but we assume this didn't happen.
        let can_reshard = next_epoch_info.last_resharding().is_none_or(|last_resharding| {
            next_epoch_info.epoch_height() - last_resharding >= min_epochs_between_resharding.get()
        });
        Ok(can_reshard)
    }
```

**File:** chain/epoch-manager/src/lib.rs (L926-929)
```rust
        let has_same_shard_layout = next_next_shard_layout == next_shard_layout;
        let last_resharding = (!has_same_shard_layout)
            .then(|| next_epoch_info.epoch_height() + 1)
            .or_else(|| next_epoch_info.last_resharding());
```

**File:** chain/epoch-manager/src/lib.rs (L2205-2210)
```rust
        // Check if resharding is allowed based on epoch constraints
        let can_reshard = self
            .can_reshard(&parent_hash, dynamic_resharding_config.min_epochs_between_resharding)?;
        if !can_reshard {
            return Ok(None);
        }
```

**File:** chain/chain/src/runtime/mod.rs (L603-605)
```rust
        if !self.epoch_manager.can_reshard(prev_block_hash, config.min_epochs_between_resharding)? {
            return Ok(None);
        }
```

**File:** docs/architecture/how/dynamic_resharding.md (L96-99)
```markdown
2. Calls `get_upcoming_shard_split()` which:
   - Checks if dynamic resharding is enabled (via `ShardLayoutConfig::Dynamic`).
   - Checks the resharding cooldown (`can_reshard()` -- verifies `epoch_height - last_resharding >= min_epochs_between_resharding`). `min_epochs_between_resharding` must be `> 0`: allowing back-to-back reshardings is unsafe because a freshly-created child shard would inherit `proposed_split` from the parent's final chunk while its own first chunk freshly computes `proposed_split = None`, triggering `InvalidChunkHeaderShardSplit`.
   - Calls `pick_shard_to_split()` to select the winning shard: forced shards have priority, otherwise the shard with highest `total_memory()` wins.
```

**File:** docs/architecture/how/dynamic_resharding.md (L119-120)
```markdown
- **Chunk header validation**: During state witness validation, the `proposed_split` in the received chunk header is compared against the locally-computed `ChunkExtra.proposed_split()`. Mismatch produces `InvalidChunkHeaderShardSplit`.
- **Block header validation**: During block processing, the `shard_split` in the block header is recomputed by calling `get_upcoming_shard_split()` with the block's chunk headers. Mismatch produces `InvalidBlockHeaderShardSplit`.
```

**File:** chain/chain/src/resharding/flat_storage_resharder.rs (L108-112)
```rust
        let status = self.runtime.store().flat_store().get_flat_storage_status(event.parent_shard);
        let FlatStorageStatus::Ready(FlatStorageReadyStatus { flat_head }) = status else {
            tracing::error!(target: "resharding", ?status, ?event, "flat storage shard split task: parent shard is not ready");
            panic!("impossible to recover from a flat storage split shard failure!");
        };
```

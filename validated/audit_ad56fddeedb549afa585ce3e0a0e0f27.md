### Title
`finalize_epoch` fallback paths skip `shard_layout` and `last_resharding` update in `EpochInfoV5` — (`File: chain/epoch-manager/src/lib.rs`)

### Summary

In `EpochManager::finalize_epoch`, two error-recovery branches that clone `next_epoch_info` as a fallback for epoch N+2 only bump `epoch_height` and silently discard the freshly-computed `next_next_shard_layout` and `last_resharding` values. When `DynamicResharding` is active and a shard split was scheduled for epoch N+2, the stored `EpochInfoV5` for that epoch carries the **old** shard layout and the **old** `last_resharding` timestamp, making the protocol's authoritative shard-layout source diverge from the block header that already committed the split.

### Finding Description

`finalize_epoch` computes two critical values before calling `proposals_to_epoch_info`:

```rust
// chain/epoch-manager/src/lib.rs  lines 926-929
let has_same_shard_layout = next_next_shard_layout == next_shard_layout;
let last_resharding = (!has_same_shard_layout)
    .then(|| next_epoch_info.epoch_height() + 1)
    .or_else(|| next_epoch_info.last_resharding());
```

Both values are passed into `proposals_to_epoch_info` (lines 938-950). If that call fails, two fallback arms execute:

```rust
// lines 952-963
Err(EpochError::ThresholdError { .. }) => {
    let mut epoch_info = EpochInfo::clone(&next_epoch_info);
    *epoch_info.epoch_height_mut() += 1;
    epoch_info                          // ← shard_layout and last_resharding NOT updated
}
Err(EpochError::NotEnoughValidators { .. }) => {
    let mut epoch_info = EpochInfo::clone(&next_epoch_info);
    *epoch_info.epoch_height_mut() += 1;
    epoch_info                          // ← same omission
}
```

The fallback clones `next_epoch_info` (epoch N+1's `EpochInfoV5`) and only increments `epoch_height`. It does **not** write `next_next_shard_layout` into `EpochInfoV5::shard_layout`, and does **not** write the updated `last_resharding` value. The resulting object is then persisted as the authoritative epoch info for epoch N+2 via `save_epoch_info`.

`EpochInfoV5` is the sole authoritative source for shard layouts under `DynamicResharding`:

```rust
// core/primitives/src/epoch_info.rs  lines 697-701
pub fn shard_layout(&self) -> Option<&ShardLayout> {
    match self {
        Self::V5(v5) => Some(&v5.shard_layout),
        _ => None,
    }
}
```

The resharding cooldown gate reads the same struct:

```rust
// chain/epoch-manager/src/lib.rs  lines 845-847
let can_reshard = next_epoch_info.last_resharding().is_none_or(|last_resharding| {
    next_epoch_info.epoch_height() - last_resharding >= min_epochs_between_resharding.get()
});
```

### Impact Explanation

**Shard-layout divergence (Critical):** The last block of epoch N already has `shard_split: Some(shard_id, boundary_account)` committed in `BlockHeaderInnerRestV6`. Every node that calls `finalize_epoch` on that block will derive the same `next_next_shard_layout` (a new `ShardLayoutV3`). If the fallback fires, the stored `EpochInfoV5.shard_layout` for epoch N+2 is the old layout, while the block header permanently records a split. Nodes that later re-derive the expected layout from the block header will disagree with the stored epoch info, causing a protocol-level state inconsistency and potential chain split.

**Resharding cooldown bypass:** Because `last_resharding` is not written into the fallback `EpochInfoV5`, `can_reshard()` returns `true` immediately in the next epoch, violating the invariant that `min_epochs_between_resharding > 0` must separate consecutive reshardings. The documentation explicitly warns that back-to-back reshardings are unsafe because a freshly-created child shard would inherit `proposed_split` from the parent's final chunk.

### Likelihood Explanation

The `ThresholdError` and `NotEnoughValidators` fallbacks are triggered when all active validators simultaneously unstake (the code comments say "all validators tried to unstake?"). This is a low-probability event in normal operation. However:

1. `DynamicResharding` is stabilized at protocol version 85 (below `STABLE_PROTOCOL_VERSION = 86`), so the code path is live on mainnet once `DynamicReshardingConfig` is populated in the epoch config.
2. The fallback arms are explicitly coded and logged, confirming they are reachable production paths.
3. No guard prevents the fallback from firing during an epoch where `shard_split` was already committed to the block header.

### Recommendation

In both fallback arms, apply the same `shard_layout` and `last_resharding` updates that `proposals_to_epoch_info` would have applied:

```rust
Err(EpochError::ThresholdError { stake_sum, num_seats }) => {
    tracing::warn!(...);
    let mut epoch_info = EpochInfo::clone(&next_epoch_info);
    *epoch_info.epoch_height_mut() += 1;
    // Apply the same shard-layout and cooldown updates the normal path would have applied.
    if let EpochInfo::V5(ref mut v5) = epoch_info {
        v5.shard_layout = next_next_shard_layout.clone();
        v5.last_resharding = last_resharding;
    }
    epoch_info
}
// identical fix for NotEnoughValidators arm
```

### Proof of Concept

1. Enable `DynamicResharding` with a `DynamicReshardingConfig` that has a low `memory_usage_threshold` so a split is proposed every epoch.
2. At the last block of epoch N, ensure `shard_split` is committed to the block header (confirmed by `validate_block_shard_split`).
3. Arrange for all validators to submit unstake proposals so that `proposals_to_epoch_info` returns `EpochError::ThresholdError` or `EpochError::NotEnoughValidators` during `finalize_epoch`.
4. Observe that the stored `EpochInfoV5` for epoch N+2 has `shard_layout` equal to the epoch N+1 layout (old layout), while the block header for the last block of epoch N has `shard_split = Some(...)`.
5. Observe that `can_reshard()` returns `true` immediately in epoch N+1, bypassing the cooldown.

The divergent Borsh bytes are the `shard_layout` field of the `EpochInfoV5` struct stored under `DBCol::EpochInfo` keyed by the epoch N+2 `EpochId`: the stored value Borsh-encodes the old `ShardLayoutV2` discriminant and boundary accounts, while the correct value should encode the new `ShardLayoutV3` with the derived split history. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** chain/epoch-manager/src/lib.rs (L918-963)
```rust
        let next_next_shard_layout = self.next_next_shard_layout(
            &epoch_config,
            epoch_protocol_version,
            &next_next_epoch_config,
            &next_shard_layout,
            block_info,
        )?;

        let has_same_shard_layout = next_next_shard_layout == next_shard_layout;
        let last_resharding = (!has_same_shard_layout)
            .then(|| next_epoch_info.epoch_height() + 1)
            .or_else(|| next_epoch_info.last_resharding());

        let strategy = AssignmentStrategy::select(
            next_next_epoch_version,
            &next_shard_layout,
            &next_next_shard_layout,
        );
        RESHARDING_ASSIGNMENT_STRATEGY.with_label_values(&[strategy.metrics_label()]).inc();

        let next_next_epoch_info = match proposals_to_epoch_info(
            &next_next_epoch_config,
            rng_seed,
            &next_epoch_info,
            all_proposals,
            validator_kickout,
            validator_reward,
            minted_amount,
            next_next_epoch_version,
            next_next_shard_layout.clone(),
            &strategy,
            last_resharding,
        ) {
            Ok(next_next_epoch_info) => next_next_epoch_info,
            Err(EpochError::ThresholdError { stake_sum, num_seats }) => {
                tracing::warn!(target: "epoch_manager", %stake_sum, %num_seats, "not enough stake for required number of seats (all validators tried to unstake?)");
                let mut epoch_info = EpochInfo::clone(&next_epoch_info);
                *epoch_info.epoch_height_mut() += 1;
                epoch_info
            }
            Err(EpochError::NotEnoughValidators { num_validators, num_shards }) => {
                tracing::warn!(target: "epoch_manager", %num_validators, %num_shards, "not enough validators for required number of shards (all validators tried to unstake?)");
                let mut epoch_info = EpochInfo::clone(&next_epoch_info);
                *epoch_info.epoch_height_mut() += 1;
                epoch_info
            }
```

**File:** core/primitives/src/epoch_info.rs (L49-71)
```rust
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

**File:** core/primitives/src/epoch_info.rs (L697-711)
```rust
    pub fn shard_layout(&self) -> Option<&ShardLayout> {
        match self {
            Self::V5(v5) => Some(&v5.shard_layout),
            _ => None,
        }
    }

    /// Get the epoch height at which the most recent resharding occurred.
    /// Returns `None` for pre-V5 `EpochInfo` or when no resharding has happened.
    pub fn last_resharding(&self) -> Option<EpochHeight> {
        match self {
            Self::V1(_) | Self::V2(_) | Self::V3(_) | Self::V4(_) => None,
            Self::V5(v5) => v5.last_resharding,
        }
    }
```

### Title
`EpochInfoV5.shard_layout` and `last_resharding` Invariants Broken in `finalize_epoch()` Fallback Paths — (`chain/epoch-manager/src/lib.rs`)

### Summary

In `finalize_epoch()`, when `proposals_to_epoch_info()` fails with `EpochError::ThresholdError` or `EpochError::NotEnoughValidators`, the code falls back to cloning `next_epoch_info` and only bumping `epoch_height`. The newly computed `next_next_shard_layout` and `last_resharding` — which were correctly derived just above — are **never applied** to the fallback `EpochInfo`. This breaks the invariant that `EpochInfoV5.shard_layout` always reflects the authoritative shard layout for that epoch, and that `EpochInfoV5.last_resharding` correctly tracks when the most recent resharding occurred.

### Finding Description

In `finalize_epoch()` in `chain/epoch-manager/src/lib.rs`, the normal path correctly computes:

1. `next_next_shard_layout` — the new shard layout for epoch T+2 (potentially a freshly derived `ShardLayoutV3` if a split was proposed).
2. `last_resharding` — the epoch height of the most recent resharding, propagated or set to `next_epoch_info.epoch_height() + 1` if a split is happening.

These are passed to `proposals_to_epoch_info(...)` which constructs a proper `EpochInfoV5` with both fields set correctly.

However, when `proposals_to_epoch_info` returns `EpochError::ThresholdError` or `EpochError::NotEnoughValidators` (which happens when all validators try to unstake simultaneously), the fallback path is:

```rust
Err(EpochError::ThresholdError { stake_sum, num_seats }) => {
    let mut epoch_info = EpochInfo::clone(&next_epoch_info);
    *epoch_info.epoch_height_mut() += 1;
    epoch_info
}
Err(EpochError::NotEnoughValidators { num_validators, num_shards }) => {
    let mut epoch_info = EpochInfo::clone(&next_epoch_info);
    *epoch_info.epoch_height_mut() += 1;
    epoch_info
}
```

This clones `next_epoch_info` (epoch T+1's info) and only increments `epoch_height`. It does **not** update:
- `shard_layout` — the clone retains T+1's layout, not the newly derived `next_next_shard_layout` for T+2.
- `last_resharding` — the clone retains T+1's `last_resharding`, not the correctly computed value.

The analog to the external bug is exact: just as `userTotalStaked` was not updated when `selfStakes[staker].amount` was updated in `release()`, here `shard_layout` and `last_resharding` are not updated when `epoch_height` is bumped in the fallback path. [1](#0-0) 

The invariant that `EpochInfoV5.shard_layout` is the authoritative source of shard layouts (as documented in the dynamic resharding design) is broken: [2](#0-1) 

The `can_reshard()` function reads `last_resharding` from `EpochInfo` to enforce the cooldown between reshardings: [3](#0-2) 

And `get_shard_layout()` reads `EpochInfo::shard_layout()` as the single source of truth: [4](#0-3) 

### Impact Explanation

**Shard layout divergence**: If a resharding split was scheduled for epoch T+2 (i.e., `next_next_shard_layout != next_shard_layout`) and the fallback path fires, the stored `EpochInfoV5` for T+2 will carry T+1's old `shard_layout` instead of the newly derived `ShardLayoutV3`. All subsequent shard-layout-dependent operations — shard tracking, chunk producer assignment, trie state migration, memtrie preloading — will operate on the wrong layout. Nodes will disagree on which shards exist, causing consensus failure.

**Cooldown bypass**: If a resharding occurred in epoch T+1 (so `last_resharding = Some(T+1.epoch_height)`), but the fallback path fires for T+2, the stored `EpochInfoV5` for T+2 will carry T+1's `last_resharding` value unchanged. `can_reshard()` computes `next_epoch_info.epoch_height() - last_resharding` against the wrong epoch height, potentially allowing a second resharding before the cooldown has elapsed, violating the "one split per epoch" invariant documented as a key design principle.

**Scope**: `EpochInfo` is Borsh-serialized and stored in the DB. Once the wrong value is committed, all nodes that process this epoch boundary will store the same broken `EpochInfo`, making the divergence deterministic and permanent across the network.

### Likelihood Explanation

The fallback path fires when all validators simultaneously try to unstake (or when there are not enough validators for the required number of shards). This is an edge case but is explicitly handled in the code with a `tracing::warn!`. The `DynamicResharding` feature is gated at protocol version 85 and is active in the nightly build. The combination — a resharding epoch boundary coinciding with a mass-unstake event — is unlikely in normal operation but is reachable without any privileged role: any set of validators can submit unstake transactions. The trigger is unprivileged-user-controlled (validator stake proposals are normal transactions).

### Recommendation

In both fallback arms, after cloning `next_epoch_info` and bumping `epoch_height`, also apply the computed `next_next_shard_layout` and `last_resharding` to the fallback `EpochInfo`. For `EpochInfoV5`, this means:

```rust
Err(EpochError::ThresholdError { .. }) | Err(EpochError::NotEnoughValidators { .. }) => {
    let mut epoch_info = EpochInfo::clone(&next_epoch_info);
    *epoch_info.epoch_height_mut() += 1;
    // Also update the shard layout and last_resharding for V5
    if let EpochInfo::V5(ref mut v5) = epoch_info {
        v5.shard_layout = next_next_shard_layout.clone();
        v5.last_resharding = last_resharding;
    }
    epoch_info
}
```

Alternatively, introduce a dedicated method on `EpochInfo` to update these fields atomically, ensuring the invariant is maintained in all code paths.

### Proof of Concept

1. Network is running with `ProtocolFeature::DynamicResharding` enabled (protocol version ≥ 85).
2. A shard split is proposed at the end of epoch N (a `shard_split` is embedded in the last block header of epoch N). `next_next_shard_layout` is a new `ShardLayoutV3` with one additional shard. `last_resharding` is set to `next_epoch_info.epoch_height() + 1`.
3. Simultaneously, all validators submit unstake transactions during epoch N, so that by the time `finalize_epoch()` runs, `proposals_to_epoch_info()` returns `EpochError::NotEnoughValidators`.
4. The fallback path fires: `EpochInfo::clone(&next_epoch_info)` is stored as the `EpochInfo` for epoch T+2. This clone has `shard_layout = next_shard_layout` (the old layout, not the new `ShardLayoutV3`) and `last_resharding = next_epoch_info.last_resharding()` (the old value, not the newly computed one).
5. In epoch T+2, `get_shard_layout()` returns the old layout from `EpochInfoV5.shard_layout()`. Nodes that expected the new child shards to exist will fail to find them. The resharding state migration that was prepared during epoch T+1 (memtrie preloading, flat storage) is now inconsistent with the epoch info.
6. `can_reshard()` reads the stale `last_resharding` from the fallback `EpochInfo`, potentially allowing another resharding before the cooldown has elapsed.

The exact divergent value is: `EpochInfoV5.shard_layout` contains the Borsh bytes of the old `ShardLayoutV2` (or previous `ShardLayoutV3`) instead of the newly derived `ShardLayoutV3` with the additional split entry. [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** chain/epoch-manager/src/lib.rs (L926-963)
```rust
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

**File:** core/primitives/src/epoch_info.rs (L697-702)
```rust
    pub fn shard_layout(&self) -> Option<&ShardLayout> {
        match self {
            Self::V5(v5) => Some(&v5.shard_layout),
            _ => None,
        }
    }
```

**File:** chain/epoch-manager/src/validator_selection.rs (L165-178)
```rust
/// Select validators for next epoch and generate epoch info
pub fn proposals_to_epoch_info(
    epoch_config: &EpochConfig,
    rng_seed: RngSeed,
    prev_epoch_info: &EpochInfo,
    proposals: Vec<ValidatorStake>,
    mut validator_kickout: HashMap<AccountId, ValidatorKickoutReason>,
    validator_reward: HashMap<AccountId, Balance>,
    minted_amount: Balance,
    protocol_version: ProtocolVersion,
    shard_layout: ShardLayout,
    strategy: &AssignmentStrategy,
    last_resharding: Option<EpochHeight>,
) -> Result<EpochInfo, EpochError> {
```

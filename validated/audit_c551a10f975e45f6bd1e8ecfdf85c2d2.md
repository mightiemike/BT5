### Title
Stale `shard_layout` and `last_resharding` in `EpochInfoV5` Error-Fallback Path of `finalize_epoch` — (`chain/epoch-manager/src/lib.rs`)

### Summary

`EpochManager::finalize_epoch` computes a new `next_next_shard_layout` and a new `last_resharding` epoch-height for epoch T+2, then passes both into `proposals_to_epoch_info`. When that call fails with `ThresholdError` or `NotEnoughValidators`, the two error-fallback arms clone `next_epoch_info` (epoch T+1) and only increment `epoch_height`, silently discarding the freshly-derived shard layout and resharding epoch-height. The resulting `EpochInfoV5` stored for epoch T+2 therefore carries T+1's stale `shard_layout` and stale `last_resharding`, breaking the invariant that `EpochInfoV5.shard_layout` is the authoritative per-epoch shard layout and that `last_resharding` faithfully tracks when the most recent split was committed.

### Finding Description

In `finalize_epoch` (`chain/epoch-manager/src/lib.rs`), the following sequence occurs:

1. `next_next_shard_layout` is derived — potentially a brand-new `ShardLayoutV3` when a split was embedded in the last block of epoch T.
2. `last_resharding` is computed as `next_epoch_info.epoch_height() + 1` when the layout changed, or carried forward otherwise.
3. Both values are forwarded to `proposals_to_epoch_info`.
4. On `ThresholdError` or `NotEnoughValidators`, the fallback arms execute:

```rust
let mut epoch_info = EpochInfo::clone(&next_epoch_info);
*epoch_info.epoch_height_mut() += 1;
epoch_info
```

Neither `shard_layout` nor `last_resharding` is patched into the cloned struct before it is saved as the epoch T+2 `EpochInfo`. The computed `next_next_shard_layout` and `last_resharding` are simply dropped.

The `EpochInfoV5` struct carries both fields:

```rust
pub struct EpochInfoV5 {
    ...
    pub shard_layout: ShardLayout,
    pub last_resharding: Option<EpochHeight>,
    ...
}
```

`EpochInfo::new` only produces `V5` when `ProtocolFeature::DynamicResharding` is enabled, so the stale-state window is exactly the dynamic-resharding protocol version range.

### Impact Explanation

**`shard_layout` staleness**: `get_shard_layout` for epoch T+2 returns T+1's layout. The resharding that was consensus-agreed via `shard_split` in the last block of epoch T is silently abandoned. Any downstream consumer that relies on `EpochInfo::shard_layout()` as the authoritative source (per the design documented in `docs/architecture/how/dynamic_resharding.md`) receives the wrong layout.

**`last_resharding` staleness**: `can_reshard` reads `next_epoch_info.last_resharding()` to enforce the cooldown invariant `epoch_height - last_resharding >= min_epochs_between_resharding`. If the fallback fires on an epoch where a split was scheduled, `last_resharding` is not advanced to `next_epoch_info.epoch_height() + 1`. Subsequent calls to `can_reshard` compute the cooldown from the wrong baseline, potentially allowing a back-to-back resharding that the documentation explicitly marks as unsafe ("allowing back-to-back reshardings is unsafe because a freshly-created child shard would inherit `proposed_split` from the parent's final chunk while its own first chunk freshly computes `proposed_split = None`, triggering `InvalidChunkHeaderShardSplit`").

### Likelihood Explanation

`NotEnoughValidators` is the more reachable trigger: dynamic resharding automatically increases the shard count, and if the validator set is near the minimum-per-shard threshold, the newly derived layout (with one extra shard) can push `proposals_to_epoch_info` into the `NotEnoughValidators` branch on the very epoch the split was scheduled. No privileged action is required — the split is selected automatically by `get_upcoming_shard_split` based on trie memory usage. `ThresholdError` (all validators unstaking simultaneously) is rarer but also reachable.

### Recommendation

In both error-fallback arms, after cloning `next_epoch_info` and incrementing `epoch_height`, also apply the computed shard-layout and resharding state:

```rust
Err(EpochError::ThresholdError { .. }) | Err(EpochError::NotEnoughValidators { .. }) => {
    let mut epoch_info = EpochInfo::clone(&next_epoch_info);
    *epoch_info.epoch_height_mut() += 1;
    // Preserve the derived shard layout and resharding epoch for V5
    if let EpochInfo::V5(ref mut v5) = epoch_info {
        v5.shard_layout = next_next_shard_layout.clone();
        v5.last_resharding = last_resharding;
    }
    epoch_info
}
```

### Proof of Concept

1. Enable `ProtocolFeature::DynamicResharding` (protocol version ≥ 153).
2. Configure `min_validators_per_shard = 2` and run with exactly `2 * N` validators for `N` shards.
3. Trigger a dynamic resharding (memory threshold exceeded) so `shard_split` is embedded in the last block of epoch T and `next_next_shard_layout` has `N+1` shards.
4. At the same epoch boundary, have one validator unstake so the validator count drops to `2*N - 1`, which is below `min_validators_per_shard * (N+1)`.
5. `proposals_to_epoch_info` returns `Err(EpochError::NotEnoughValidators { .. })`.
6. Inspect the stored `EpochInfo` for epoch T+2: `shard_layout` equals T+1's layout (split discarded) and `last_resharding` equals T+1's value (cooldown baseline stale).
7. In the next epoch, `can_reshard` computes the cooldown from the stale `last_resharding`, allowing an immediate re-split that violates the documented safety invariant.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** core/primitives/src/epoch_info.rs (L46-71)
```rust
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

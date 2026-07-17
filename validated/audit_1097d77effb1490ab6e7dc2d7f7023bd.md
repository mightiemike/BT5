### Title
`last_resharding` Not Updated in `finalize_epoch` Fallback Path, Bypassing Resharding Cooldown Invariant — (File: `chain/epoch-manager/src/lib.rs`)

---

### Summary

In `EpochManager::finalize_epoch`, the correct `last_resharding` epoch-height is computed before calling `proposals_to_epoch_info`. When that call fails with `ThresholdError` or `NotEnoughValidators`, the two fallback arms clone `next_epoch_info` and only increment `epoch_height`, but never write the newly computed `last_resharding` into the cloned struct. The stored `EpochInfoV5` for epoch N+2 therefore carries a stale `last_resharding` value. `can_reshard()` reads exactly that field to enforce the cooldown, so the cooldown invariant is silently broken for the affected epoch.

---

### Finding Description

`finalize_epoch` computes `last_resharding` at lines 927–929:

```rust
let has_same_shard_layout = next_next_shard_layout == next_shard_layout;
let last_resharding = (!has_same_shard_layout)
    .then(|| next_epoch_info.epoch_height() + 1)
    .or_else(|| next_epoch_info.last_resharding());
``` [1](#0-0) 

This value is passed correctly to `proposals_to_epoch_info`. However, when that function returns `ThresholdError` or `NotEnoughValidators`, the fallback arms are:

```rust
Err(EpochError::ThresholdError { .. }) => {
    let mut epoch_info = EpochInfo::clone(&next_epoch_info);
    *epoch_info.epoch_height_mut() += 1;
    epoch_info   // last_resharding NOT updated
}
Err(EpochError::NotEnoughValidators { .. }) => {
    let mut epoch_info = EpochInfo::clone(&next_epoch_info);
    *epoch_info.epoch_height_mut() += 1;
    epoch_info   // last_resharding NOT updated
}
``` [2](#0-1) 

The clone inherits `next_epoch_info.last_resharding()` — the epoch N+1 value — not the freshly computed `last_resharding` that would have been `Some(next_epoch_info.epoch_height() + 1)` when a split was scheduled. This cloned struct is then saved as the authoritative `EpochInfoV5` for epoch N+2. [3](#0-2) 

`can_reshard()` enforces the cooldown by reading `next_epoch_info.last_resharding()` from the stored epoch info:

```rust
let can_reshard = next_epoch_info.last_resharding().is_none_or(|last_resharding| {
    next_epoch_info.epoch_height() - last_resharding >= min_epochs_between_resharding.get()
});
``` [4](#0-3) 

Because the stored epoch N+2 info has the wrong `last_resharding`, the cooldown arithmetic uses a stale epoch height, and `can_reshard` returns `true` when it should return `false`.

The `last_resharding` field lives in `EpochInfoV5`:

```rust
pub struct EpochInfoV5 {
    ...
    pub last_resharding: Option<EpochHeight>,
    ...
}
``` [5](#0-4) 

`DynamicResharding` is stabilized at protocol version 85 (the current `STABLE_PROTOCOL_VERSION`), so this code is active in production. [6](#0-5) 

---

### Impact Explanation

The resharding cooldown is the sole protocol-level guard against back-to-back shard splits. The design documentation explicitly states:

> `min_epochs_between_resharding` must be `> 0`: allowing back-to-back reshardings is unsafe because a freshly-created child shard would inherit `proposed_split` from the parent's final chunk while its own first chunk freshly computes `proposed_split = None`, triggering `InvalidChunkHeaderShardSplit`. [7](#0-6) 

If the cooldown is bypassed, a second split can be scheduled in the very next eligible epoch. The resulting `InvalidChunkHeaderShardSplit` error during chunk validation would cause block processing to fail, halting the chain for all nodes tracking the affected shard.

---

### Likelihood Explanation

The trigger requires two simultaneous conditions during a dynamic-resharding epoch:

1. A shard split is scheduled (`has_same_shard_layout == false`).
2. `proposals_to_epoch_info` fails with `ThresholdError` (total stake < number of seats) or `NotEnoughValidators` (fewer validators than shards).

Condition 2 corresponds to a mass-unstake event. While rare under normal operation, it is reachable without any privileged or malicious action — validators exercising their normal right to unstake can collectively trigger it. The combination of both conditions in the same epoch is low-probability but not impossible, particularly during network stress or coordinated validator exits.

---

### Recommendation

In both fallback arms, after cloning `next_epoch_info` and incrementing `epoch_height`, explicitly set `last_resharding` to the value already computed at line 927–929. For `EpochInfo::V5`, this requires a setter or direct field mutation analogous to `epoch_height_mut()`. The fix is:

```rust
Err(EpochError::ThresholdError { .. }) => {
    let mut epoch_info = EpochInfo::clone(&next_epoch_info);
    *epoch_info.epoch_height_mut() += 1;
    // FIX: propagate the computed last_resharding
    if let EpochInfo::V5(ref mut v5) = epoch_info {
        v5.last_resharding = last_resharding;
    }
    epoch_info
}
// same for NotEnoughValidators arm
```

Alternatively, expose a `last_resharding_mut()` method on `EpochInfo` mirroring `epoch_height_mut()`.

---

### Proof of Concept

**Epoch sequence:**

| Epoch | Event |
|-------|-------|
| N | Shard split proposed; `block_info.shard_split()` is `Some(...)` |
| N (finalize) | `next_next_shard_layout != next_shard_layout` → `last_resharding = Some(epoch_N1_height + 1)` |
| N (finalize) | `proposals_to_epoch_info` returns `ThresholdError` (mass unstake) |
| N (finalize) | Fallback clones epoch N+1 info; `last_resharding` stays at epoch N+1's stale value (e.g., `None`) |
| N+2 | `can_reshard()` reads `last_resharding = None` → `is_none_or(...)` returns `true` |
| N+2 | A second split is immediately schedulable, violating the cooldown |
| N+2 (execute) | `InvalidChunkHeaderShardSplit` on the freshly split child shard → chain halt |

The divergent Borsh-serialized value is `EpochInfoV5.last_resharding` stored in `DBCol::EpochInfo` for epoch N+2: it encodes `None` (or a prior epoch height) instead of `Some(epoch_N2_height)`. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** chain/epoch-manager/src/lib.rs (L833-848)
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
```

**File:** chain/epoch-manager/src/lib.rs (L926-965)
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
            Err(err) => return Err(err),
        };
```

**File:** chain/epoch-manager/src/lib.rs (L966-977)
```rust
        let next_next_epoch_id = EpochId(*last_block_hash);
        tracing::debug!(
            target: "epoch_manager",
            next_next_epoch_height = %next_next_epoch_info.epoch_height(),
            ?next_next_epoch_id,
            next_next_protocol_version = %next_next_epoch_info.protocol_version(),
            ?next_next_shard_layout,
            ?next_next_epoch_config,
        );
        // This epoch info is computed for the epoch after next (T+2),
        // where epoch_id of it is the hash of last block in this epoch (T).
        self.save_epoch_info(store_update, &next_next_epoch_id, Arc::new(next_next_epoch_info))?;
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

**File:** docs/architecture/how/dynamic_resharding.md (L97-99)
```markdown
   - Checks if dynamic resharding is enabled (via `ShardLayoutConfig::Dynamic`).
   - Checks the resharding cooldown (`can_reshard()` -- verifies `epoch_height - last_resharding >= min_epochs_between_resharding`). `min_epochs_between_resharding` must be `> 0`: allowing back-to-back reshardings is unsafe because a freshly-created child shard would inherit `proposed_split` from the parent's final chunk while its own first chunk freshly computes `proposed_split = None`, triggering `InvalidChunkHeaderShardSplit`.
   - Calls `pick_shard_to_split()` to select the winning shard: forced shards have priority, otherwise the shard with highest `total_memory()` wins.
```

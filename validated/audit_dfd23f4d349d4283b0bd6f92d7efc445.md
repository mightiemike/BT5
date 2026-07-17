### Title
`finalize_epoch()` fallback paths omit `shard_layout` and `last_resharding` updates when `proposals_to_epoch_info` fails during a scheduled dynamic resharding — (`chain/epoch-manager/src/lib.rs`)

---

### Summary

In `EpochManager::finalize_epoch()`, the code correctly derives `next_next_shard_layout` and a new `last_resharding` value before calling `proposals_to_epoch_info()`. However, when `proposals_to_epoch_info()` fails with `EpochError::ThresholdError` or `EpochError::NotEnoughValidators`, the two fallback arms clone `next_epoch_info` (epoch N+1's `EpochInfoV5`) and only increment the epoch height — they never write the freshly computed `next_next_shard_layout` or `last_resharding` into the cloned struct. The result is that epoch N+2's `EpochInfo` is persisted with a stale shard layout and a stale resharding-cooldown timestamp, even though the last block of epoch N has already committed a `shard_split` decision in its header. Every downstream consumer that reads `EpochInfo::shard_layout()` or `EpochInfo::last_resharding()` — including `get_shard_layout()` and `can_reshard()` — then operates on incorrect state.

---

### Finding Description

**Exact divergent code — `chain/epoch-manager/src/lib.rs` lines 918–963:**

`finalize_epoch()` first computes the correct values for epoch N+2:

```rust
let next_next_shard_layout = self.next_next_shard_layout(
    &epoch_config, epoch_protocol_version,
    &next_next_epoch_config, &next_shard_layout, block_info,
)?;                                                          // may be a new ShardLayoutV3

let has_same_shard_layout = next_next_shard_layout == next_shard_layout;
let last_resharding = (!has_same_shard_layout)
    .then(|| next_epoch_info.epoch_height() + 1)            // updated cooldown timestamp
    .or_else(|| next_epoch_info.last_resharding());
```

These values are passed to `proposals_to_epoch_info()`. On success, `EpochInfo::new()` stores them in `EpochInfoV5.shard_layout` and `EpochInfoV5.last_resharding`. On failure, both fallback arms do:

```rust
Err(EpochError::ThresholdError { .. }) | Err(EpochError::NotEnoughValidators { .. }) => {
    let mut epoch_info = EpochInfo::clone(&next_epoch_info);  // clones epoch N+1's EpochInfoV5
    *epoch_info.epoch_height_mut() += 1;                      // only field updated
    epoch_info                                                 // shard_layout and last_resharding NOT updated
}
```

`next_epoch_info` is epoch N+1's `EpochInfoV5`. Its `shard_layout` field holds the **old** layout (epoch N+1's), not `next_next_shard_layout`. Its `last_resharding` field holds epoch N+1's cooldown timestamp, not the freshly computed one. The cloned struct is then saved as epoch N+2's authoritative `EpochInfo` via `self.save_epoch_info(store_update, &next_next_epoch_id, ...)`.

**Dependent systems that read the stale fields:**

1. `get_shard_layout()` (`chain/epoch-manager/src/lib.rs` line 1759–1771) reads `epoch_info.shard_layout()` as the single source of truth for dynamic-resharding epochs. It returns the old layout for epoch N+2.

2. `can_reshard()` (`chain/epoch-manager/src/lib.rs` lines 833–848) reads `next_epoch_info.last_resharding()` to enforce the cooldown invariant `epoch_height - last_resharding >= min_epochs_between_resharding`. With the stale value, the cooldown check is wrong.

**The block-header commitment is already on-chain.** `block_info.shard_split()` was embedded in `BlockHeaderInnerRestV6` and validated by `validate_block_shard_split()` before `finalize_epoch()` is called. The header says a split was scheduled; the persisted `EpochInfo` says it was not. These two authoritative sources diverge.

---

### Impact Explanation

**Wrong shard layout for epoch N+2 (High/Critical):** `get_shard_layout()` returns the old `ShardLayoutV3` (or V2) for epoch N+2. All shard-layout-dependent logic — account-to-shard routing, `ShardTracker::cares_about_shard`, `get_resharding_parent_shard_uid`, flat-storage creation for child shards, memtrie pre-loading — operates on the wrong layout. Nodes that correctly derive the new layout from the block header will disagree with nodes that read from `EpochInfo`, producing a consensus split.

**Broken resharding cooldown (High):** `can_reshard()` reads the stale `last_resharding`. If a resharding was scheduled (`has_same_shard_layout == false`) but the fallback fires, the cooldown is not advanced. The next epoch boundary will see `last_resharding` as if no resharding occurred, allowing an immediate second resharding. The codebase explicitly documents this as unsafe: *"allowing back-to-back reshardings is unsafe because a freshly-created child shard would inherit `proposed_split` from the parent's final chunk while its own first chunk freshly computes `proposed_split = None`, triggering `InvalidChunkHeaderShardSplit`."*

---

### Likelihood Explanation

The trigger requires two conditions to coincide in the same epoch:

1. Dynamic resharding is active and a shard split is selected (`block_info.shard_split()` is `Some`).
2. `proposals_to_epoch_info()` returns `EpochError::NotEnoughValidators` (all validators attempt to unstake simultaneously) or `EpochError::ThresholdError` (total stake falls below the seat threshold).

Condition 2 is rare in a healthy mainnet but is a documented, reachable code path (the `tracing::warn!` messages confirm it is expected to occur). In a network under stress — coordinated validator exits, a slashing event that removes large stake, or a testnet/staging environment — both conditions can coincide. The fallback paths exist precisely because the protocol must not halt; the bug is that they silently corrupt the epoch-info state when dynamic resharding is in play.

---

### Recommendation

In both fallback arms, after cloning `next_epoch_info` and incrementing the epoch height, also apply the computed `next_next_shard_layout` and `last_resharding` to the cloned struct. Add setter methods on `EpochInfo` analogous to `epoch_height_mut()` for these two fields, or reconstruct the fallback epoch info via a dedicated helper that accepts the layout and cooldown as parameters. At minimum, add a `debug_assert!` that the saved epoch info's `shard_layout()` matches `next_next_shard_layout` to catch regressions.

---

### Proof of Concept

The divergent Borsh bytes are the `shard_layout` and `last_resharding` fields of the `EpochInfoV5` struct written to the `EpochInfo` DB column for epoch N+2.

**Correct path (proposals_to_epoch_info succeeds):**
- `EpochInfoV5.shard_layout` = `next_next_shard_layout` (new `ShardLayoutV3` with updated split map)
- `EpochInfoV5.last_resharding` = `Some(next_epoch_info.epoch_height() + 1)`

**Fallback path (ThresholdError or NotEnoughValidators):**
- `EpochInfoV5.shard_layout` = `next_shard_layout` (epoch N+1's old layout — wrong)
- `EpochInfoV5.last_resharding` = `next_epoch_info.last_resharding()` (epoch N+1's old cooldown — wrong)

The exact lines are: [1](#0-0) [2](#0-1) 

The stale `shard_layout` field definition in `EpochInfoV5`: [3](#0-2) 

The authoritative `get_shard_layout()` reader that will return the wrong value: [4](#0-3) 

The `can_reshard()` cooldown check that will use the stale `last_resharding`: [5](#0-4) 

The documented safety invariant that back-to-back reshardings violate: [6](#0-5)

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

**File:** chain/epoch-manager/src/lib.rs (L918-929)
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
```

**File:** chain/epoch-manager/src/lib.rs (L951-963)
```rust
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

**File:** chain/epoch-manager/src/lib.rs (L1759-1772)
```rust
    pub fn get_shard_layout(&self, epoch_id: &EpochId) -> Result<ShardLayout, EpochError> {
        let epoch_info = self.get_epoch_info(epoch_id)?;
        if let Some(shard_layout) = epoch_info.shard_layout() {
            Ok(shard_layout.clone())
        } else {
            let protocol_version = epoch_info.protocol_version();
            self.get_static_shard_layout_for_protocol_version(protocol_version).ok_or_else(|| {
                EpochError::ShardingError(format!(
                    "shard layout missing. epoch_id={:?} protocol_version={}",
                    epoch_id, protocol_version
                ))
            })
        }
    }
```

**File:** core/primitives/src/epoch_info.rs (L61-64)
```rust
    pub shard_layout: ShardLayout,
    /// The epoch height at which the most recent resharding occurred.
    /// `None` means no resharding has happened since dynamic resharding was enabled.
    pub last_resharding: Option<EpochHeight>,
```

**File:** docs/architecture/how/dynamic_resharding.md (L98-98)
```markdown
   - Checks the resharding cooldown (`can_reshard()` -- verifies `epoch_height - last_resharding >= min_epochs_between_resharding`). `min_epochs_between_resharding` must be `> 0`: allowing back-to-back reshardings is unsafe because a freshly-created child shard would inherit `proposed_split` from the parent's final chunk while its own first chunk freshly computes `proposed_split = None`, triggering `InvalidChunkHeaderShardSplit`.
```

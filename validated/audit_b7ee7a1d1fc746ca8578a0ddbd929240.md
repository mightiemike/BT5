### Title
`finalize_epoch` fallback path stores stale shard layout in `EpochInfoV5` for epoch N+2, silently dropping a scheduled dynamic resharding split - (`File: chain/epoch-manager/src/lib.rs`)

### Summary

When `DynamicResharding` is enabled (protocol version 85+) and `proposals_to_epoch_info` fails with `ThresholdError` or `NotEnoughValidators` during `finalize_epoch`, the fallback path clones `next_epoch_info` (epoch N+1's `EpochInfoV5`) and only increments `epoch_height`. It ignores the already-computed `next_next_shard_layout` — which may be a freshly derived `ShardLayoutV3` encoding a scheduled shard split. The stored `EpochInfoV5.shard_layout` for epoch N+2 is therefore the Borsh encoding of epoch N+1's layout, not the correct N+2 layout. Every subsequent `get_shard_layout(epoch_N+2)` call returns the wrong layout, silently dropping the split and corrupting the shard-layout invariant for all downstream consumers.

### Finding Description

`EpochManager::finalize_epoch` in `chain/epoch-manager/src/lib.rs` computes `next_next_shard_layout` before calling `proposals_to_epoch_info`: [1](#0-0) 

When `proposals_to_epoch_info` succeeds, the returned `EpochInfoV5` carries the correct `next_next_shard_layout` (passed as the `shard_layout` argument at line 947). But when it fails with `ThresholdError` or `NotEnoughValidators`, the fallback clones `next_epoch_info` (epoch N+1's info) and only bumps `epoch_height`: [2](#0-1) 

Because `DynamicResharding` is enabled at protocol version 85, `next_epoch_info` is `EpochInfoV5`, which carries `shard_layout` = epoch N+1's layout: [3](#0-2) 

The fallback clone therefore stores epoch N+1's `ShardLayout` as the authoritative layout for epoch N+2. The `last_resharding` field is also wrong — it reflects epoch N+1's value, not the updated value computed at line 927–929: [4](#0-3) 

`get_shard_layout` for epoch N+2 reads `EpochInfo::shard_layout()` first (V5 path), so it returns the stale layout without ever consulting `EpochConfig`: [5](#0-4) 

The `shard_layout()` accessor returns `Some` only for `V5`, so the fallback to `EpochConfig::static_shard_layout()` is never reached: [6](#0-5) 

The exact divergent Borsh bytes: `EpochInfoV5.shard_layout` for epoch N+2 is the Borsh encoding of epoch N+1's `ShardLayout` (V2 or V3 with N+1's boundary accounts and shard IDs) instead of the Borsh encoding of `next_next_shard_layout` (a new `ShardLayoutV3` with the split boundary account and two new child shard IDs).

Additionally, `chunk_producers_settlement` in the fallback clone has epoch N+1's shard count, not N+2's. If a split was scheduled, N+2 should have one more shard, so the settlement vector has the wrong length.

### Impact Explanation

- **Scheduled shard split silently dropped**: A split committed to `BlockHeaderInnerRestV6.shard_split` at the last block of epoch N is never reflected in epoch N+2's `EpochInfo`. The new child shard IDs are never allocated.
- **Shard layout divergence**: All callers of `get_shard_layout(epoch_N+2)` — including `account_id_to_shard_id`, `is_resharding_boundary`, `start_resharding`, and `maybe_start_memtrie_preload_for_resharding` — receive the wrong layout. Nodes that independently recompute the layout (e.g., during state sync or stateless validation) will disagree with the stored value, potentially causing a chain halt.
- **`last_resharding` corruption**: The cooldown check `can_reshard` reads `EpochInfoV5::last_resharding`. The fallback carries epoch N+1's value, so the cooldown counter is wrong for all future epochs, potentially allowing back-to-back reshardings (which the doc explicitly calls unsafe) or permanently blocking resharding.
- **`chunk_producers_settlement` length mismatch**: The settlement vector has N+1's shard count. Any code indexing by shard index into this vector for epoch N+2 will either panic or silently use the wrong assignment. [7](#0-6) 

### Likelihood Explanation

`ThresholdError` fires when `stake_sum < num_seats` (all or nearly all validators unstake simultaneously). `NotEnoughValidators` fires when the number of remaining validators is less than the number of shards. Both are explicitly handled as production scenarios (the warn log says "all validators tried to unstake?"). On a chain with few validators (e.g., a new chain bootstrapping with dynamic resharding already enabled, or a testnet), a coordinated unstaking event — which is a normal protocol action, not a privileged admin operation — can trigger this path. The bug only manifests when `DynamicResharding` is enabled (protocol version ≥ 85) and a shard split is concurrently scheduled, making it a narrow but reachable upgrade-boundary interaction.

### Recommendation

In both fallback arms, replace the bare clone of `next_epoch_info` with a new `EpochInfo` constructed via `EpochInfo::new(...)` using the already-computed `next_next_shard_layout` and the correct `last_resharding` value. Concretely, after computing `next_next_shard_layout` and `last_resharding` (lines 918–929), the fallback should call `EpochInfo::new` with those values rather than cloning `next_epoch_info`. At minimum, if a clone is kept for simplicity, the `shard_layout` and `last_resharding` fields of the resulting `EpochInfoV5` must be overwritten with `next_next_shard_layout` and the computed `last_resharding` before the epoch info is saved.

### Proof of Concept

1. Enable `DynamicResharding` (protocol version ≥ 85, `ShardLayoutConfig::Dynamic`).
2. At the last block of epoch N, a shard split is proposed and selected: `block_info.shard_split()` returns `Some((shard_id, boundary_account))`.
3. `finalize_epoch` computes `next_next_shard_layout` = a new `ShardLayoutV3` with one additional shard (lines 918–924).
4. All validators submit unstaking proposals, causing `proposals_to_epoch_info` to return `Err(EpochError::ThresholdError { ... })` or `Err(EpochError::NotEnoughValidators { ... })`.
5. The fallback at lines 952–963 clones `next_epoch_info` (epoch N+1's `EpochInfoV5`) and only increments `epoch_height`. The `shard_layout` field of the stored epoch N+2 info is epoch N+1's layout.
6. `get_shard_layout(epoch_N+2)` returns epoch N+1's layout. `is_resharding_boundary` returns `false` for the N+1→N+2 boundary. `maybe_start_memtrie_preload_for_resharding` does not start. The split is silently dropped.
7. Any node that independently recomputes the expected layout for epoch N+2 (e.g., via stateless validation or state sync) derives the correct split layout and disagrees with the stored value, breaking consensus. [8](#0-7) [9](#0-8)

### Citations

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

**File:** chain/epoch-manager/src/lib.rs (L926-929)
```rust
        let has_same_shard_layout = next_next_shard_layout == next_shard_layout;
        let last_resharding = (!has_same_shard_layout)
            .then(|| next_epoch_info.epoch_height() + 1)
            .or_else(|| next_epoch_info.last_resharding());
```

**File:** chain/epoch-manager/src/lib.rs (L938-965)
```rust
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

**File:** core/primitives/src/epoch_info.rs (L49-64)
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

**File:** core/primitives/src/epoch_info.rs (L697-702)
```rust
    pub fn shard_layout(&self) -> Option<&ShardLayout> {
        match self {
            Self::V5(v5) => Some(&v5.shard_layout),
            _ => None,
        }
    }
```

**File:** chain/epoch-manager/src/adapter.rs (L344-353)
```rust
    /// Given the `parent_hash` of a block, returns true if that block starts a
    /// new epoch with a different shard layout.
    fn is_resharding_boundary(&self, parent_hash: &CryptoHash) -> Result<bool, EpochError> {
        if !self.is_next_block_epoch_start(parent_hash)? {
            return Ok(false);
        }
        let shard_layout = self.get_shard_layout_from_prev_block(parent_hash)?;
        let prev_shard_layout = self.get_shard_layout(&self.get_epoch_id(parent_hash)?)?;
        Ok(shard_layout != prev_shard_layout)
    }
```

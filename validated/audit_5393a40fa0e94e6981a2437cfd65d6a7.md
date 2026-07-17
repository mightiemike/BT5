### Title
Silent `false` return in `check_if_descendant_of_tracked_shard_impl` when V2 shard-layout history has a parentless shard — (`chain/epoch-manager/src/shard_tracker.rs`)

### Summary

`check_if_descendant_of_tracked_shard_impl` traces shard ancestry through historical V1/V2 layout pairs. When `try_get_parent_shard_id` returns `Ok(None)` — which happens for any shard that pre-dates the most-recent V2 split and therefore has no entry in that layout's `shards_parent_map` — the function hits a `debug_assert!(false, …)` that fires only in debug builds, then silently returns `Ok(false)` in production. A node configured with `TrackedShardsConfig::Shards` therefore concludes it does **not** track a shard that is a legitimate descendant of a configured tracked shard, causing it to stop applying chunks for that shard after resharding.

### Finding Description

`check_if_descendant_of_tracked_shard_impl` is the production path for `TrackedShardsConfig::Shards`. It first checks for a `ShardLayoutV3` fast-path via `ancestor_uids`; when the current epoch still uses V1/V2 layouts (mainnet today), it falls through to the layout-history loop:

```rust
for window in layout_history.windows(2) {
    let current_layout = &window[0];
    let prev_layout   = &window[1];
    let Some(parent_shard_id) =
        current_layout.try_get_parent_shard_id(shard_uid.shard_id())?
    else {
        debug_assert!(
            false,
            "Parent shard is missing for shard {} in shard layout {:?}",
            shard_uid, current_layout,
        );
        return Ok(false);   // ← silent wrong answer in release builds
    };
    shard_uid = ShardUId::from_shard_id_and_layout(parent_shard_id, &prev_layout);
    if tracked_shards.contains(&shard_uid) {
        return Ok(true);
    }
}
``` [1](#0-0) 

`get_shard_layout_history` collects all distinct **static** shard layouts from genesis to the current protocol version, newest-first: [2](#0-1) 

For a V2 layout, `shards_parent_map` is derived solely from `shards_split_map`, which records only the **most-recent** split. A shard that existed before that split has no entry in `shards_parent_map`. Consequently, `try_get_parent_shard_id` returns `Ok(None)` for it when the window `[L_n, L_{n-1}]` is processed and the shard was not created in the `L_{n-1}→L_n` transition. [3](#0-2) 

The correct behaviour is to continue iterating (the shard is unchanged across that transition), but the code instead returns `Ok(false)`. The `debug_assert!` is the only guard, and it is compiled away in release builds.

The call chain is:

```
tracks_shard_at_epoch (TrackedShardsConfig::Shards arm)
  → check_if_descendant_of_tracked_shard
      → check_if_descendant_of_tracked_shard_impl   ← silent false here
``` [4](#0-3) [5](#0-4) 

### Impact Explanation

A node configured with `TrackedShardsConfig::Shards` pointing at a pre-reshard parent shard will, after the network reshards, silently evaluate `tracks_shard_at_epoch` as `false` for every child shard. Consequences:

- The node stops applying chunks for those shards.
- It stops participating in chunk validation for those shards.
- It drifts out of sync without any error log (the `debug_assert!` is silent in release).

This is a **High** severity correctness/availability impact: affected nodes silently diverge from the canonical chain state.

### Likelihood Explanation

Mainnet currently runs V2 shard layouts (Simple Nightshade V2, 5 shards). Any node operator using `TrackedShardsConfig::Shards` with a shard that pre-dates the most recent static resharding (e.g., shard 0, which existed before the V1→V2 split) will trigger this path on the next layout-history traversal. No privileged role is required; the configuration is a standard node option.

### Recommendation

Replace the `debug_assert!` + `return Ok(false)` with a `continue`, so that when a shard has no parent in the current layout transition the loop advances to the next window with the same `shard_uid`:

```rust
let Some(parent_shard_id) =
    current_layout.try_get_parent_shard_id(shard_uid.shard_id())?
else {
    // Shard was not created in this transition; carry it forward unchanged.
    continue;
};
```

Alternatively, add a hard `Err(...)` return so the failure is visible in production rather than silently wrong.

### Proof of Concept

1. Configure a node with `TrackedShardsConfig::Shards` containing `ShardUId` for shard 0 under the V1 layout (version byte 1).
2. Let the network perform a static resharding (V1 → V2, adding a 5th shard).
3. Call `tracks_shard_at_epoch` for shard 0 in the new V2 epoch.
4. `check_if_descendant_of_tracked_shard_impl` enters the layout-history loop with `layout_history = [L_V2, L_V1, L_V0]`.
5. Window `[L_V2, L_V1]`: `try_get_parent_shard_id(0)` on `L_V2` returns `Ok(None)` because shard 0 was not created in the V1→V2 split (it pre-existed).
6. `debug_assert!` is compiled away; function returns `Ok(false)`.
7. Node stops tracking shard 0 and all its descendants, silently diverging from the chain. [6](#0-5) [2](#0-1) [7](#0-6)

### Citations

**File:** chain/epoch-manager/src/shard_tracker.rs (L83-111)
```rust
    fn tracks_shard_at_epoch(
        &self,
        shard_id: ShardId,
        epoch_id: &EpochId,
    ) -> Result<bool, EpochError> {
        // TODO(#13445): Add a debug assertion that shard exists in the epoch.
        match &self.tracked_shards_config {
            TrackedShardsConfig::NoShards => Ok(false),
            TrackedShardsConfig::AllShards => Ok(true),
            TrackedShardsConfig::Shards(tracked_shards) => {
                // TODO(#13445): Turn the check below into a debug assert and call it earlier,
                // for all `tracked_shards_config` variants.
                let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;
                if !shard_layout.shard_ids().contains(&shard_id) {
                    return Ok(false);
                }
                self.check_if_descendant_of_tracked_shard(shard_id, tracked_shards, epoch_id)
            }
            TrackedShardsConfig::Accounts(tracked_accounts) => {
                self.check_if_shard_contains_tracked_account(shard_id, tracked_accounts, epoch_id)
            }
            TrackedShardsConfig::Schedule(schedule) => {
                self.check_if_shard_is_tracked_according_to_schedule(shard_id, schedule, epoch_id)
            }
            TrackedShardsConfig::ShadowValidator(account_id) => {
                self.epoch_manager.cares_about_shard_in_epoch(epoch_id, account_id, shard_id)
            }
        }
    }
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L531-552)
```rust
    /// Checks whether `shard_id` is a descendant of any of the `tracked_shards`.
    /// Assumes that `shard_id` exists in the shard layout of `epoch_id`.
    pub fn check_if_descendant_of_tracked_shard(
        &self,
        shard_id: ShardId,
        tracked_shards: &Vec<ShardUId>,
        epoch_id: &EpochId,
    ) -> Result<bool, EpochError> {
        if let Some(is_tracked) = self.descendant_of_tracked_shard_cache.lock().get(&shard_id) {
            return Ok(*is_tracked);
        }

        let is_tracked = check_if_descendant_of_tracked_shard_impl(
            shard_id,
            &tracked_shards,
            &epoch_id,
            &self.epoch_manager,
        )?;

        self.descendant_of_tracked_shard_cache.lock().insert(shard_id, is_tracked);
        Ok(is_tracked)
    }
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L571-615)
```rust
fn check_if_descendant_of_tracked_shard_impl(
    shard_id: ShardId,
    tracked_shards: &Vec<ShardUId>,
    epoch_id: &EpochId,
    epoch_manager: &Arc<dyn EpochManagerAdapter>,
) -> Result<bool, EpochError> {
    let tracked_shards: HashSet<ShardUId> = tracked_shards.into_iter().cloned().collect();
    let protocol_version = epoch_manager.get_epoch_protocol_version(epoch_id)?;
    let shard_layout = epoch_manager.get_shard_layout(&epoch_id)?;

    // `ShardLayoutV3` stores all ancestor shards, no need to iterate through protocol versions
    if let Some(ancestors) = shard_layout.ancestor_uids(shard_id) {
        let ancestors = HashSet::from_iter(ancestors);
        return Ok(!ancestors.is_disjoint(&tracked_shards));
    }

    let mut shard_uid = ShardUId::from_shard_id_and_layout(shard_id, &shard_layout);
    if tracked_shards.contains(&shard_uid) {
        // We explicitly track `shard_id` (the shard is a descendant of itself).
        return Ok(true);
    }

    // `shard_uid` does not belong to `tracked_shards`, but it might be a descendant of one.
    // Iterate through consecutive pairs of historical shard layouts (newest to oldest) to trace
    // the ancestry. Each pair represents a resharding transition.
    let layout_history = epoch_manager.get_shard_layout_history(protocol_version, None);
    for window in layout_history.windows(2) {
        let current_layout = &window[0];
        let prev_layout = &window[1];
        let Some(parent_shard_id) = current_layout.try_get_parent_shard_id(shard_uid.shard_id())?
        else {
            debug_assert!(
                false,
                "Parent shard is missing for shard {} in shard layout {:?}",
                shard_uid, current_layout,
            );
            return Ok(false);
        };
        shard_uid = ShardUId::from_shard_id_and_layout(parent_shard_id, &prev_layout);
        if tracked_shards.contains(&shard_uid) {
            return Ok(true);
        }
    }
    Ok(false)
}
```

**File:** chain/epoch-manager/src/lib.rs (L805-826)
```rust
    fn get_shard_layout_history(
        &self,
        latest_protocol_version: ProtocolVersion,
        earliest_protocol_version: Option<ProtocolVersion>,
    ) -> Vec<ShardLayout> {
        let mut layouts = Vec::new();
        let earliest_protocol_version =
            earliest_protocol_version.unwrap_or_else(|| self.config.genesis_protocol_version());

        for version in (earliest_protocol_version..=latest_protocol_version).rev() {
            // Skip protocol versions with dynamic layout
            let Some(layout) = self.get_static_shard_layout_for_protocol_version(version) else {
                continue;
            };
            // avoid duplicates if layout doesn't change
            if layouts.last() != Some(&layout) {
                layouts.push(layout);
            }
        }

        layouts
    }
```

**File:** core/primitives/src/shard_layout/v2.rs (L198-230)
```rust
impl ShardLayoutV2 {
    pub fn new(
        boundary_accounts: Vec<AccountId>,
        shard_ids: Vec<ShardId>,
        shards_split_map: Option<ShardsSplitMapV2>,
    ) -> Self {
        // In the v2 layout the version is not updated with every shard layout.
        const VERSION: ShardVersion = 3;

        assert_eq!(boundary_accounts.len() + 1, shard_ids.len());
        assert_eq!(boundary_accounts, boundary_accounts.iter().sorted().cloned().collect_vec());

        let mut id_to_index_map = BTreeMap::new();
        let mut index_to_id_map = BTreeMap::new();
        for (shard_index, &shard_id) in shard_ids.iter().enumerate() {
            id_to_index_map.insert(shard_id, shard_index);
            index_to_id_map.insert(shard_index, shard_id);
        }

        let shards_parent_map = shards_split_map.as_ref().map(|shards_split_map| {
            validate_and_derive_shard_parent_map(&shard_ids, &shards_split_map)
        });

        Self {
            boundary_accounts,
            shard_ids,
            id_to_index_map,
            index_to_id_map,
            shards_split_map,
            shards_parent_map,
            version: VERSION,
        }
    }
```

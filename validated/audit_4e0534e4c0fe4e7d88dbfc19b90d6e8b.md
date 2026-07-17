### Title
`descendant_of_tracked_shard_cache` keyed only by `ShardId`, not by epoch — stale `false` survives resharding and silently drops child-shard tracking - (File: chain/epoch-manager/src/shard_tracker.rs)

---

### Summary

`ShardTracker::check_if_descendant_of_tracked_shard` caches its result in a `HashMap<ShardId, bool>` that is keyed solely by the bare numeric `ShardId`. The cache is never cleared or invalidated. After a resharding, the same `ShardId` reappears in the new epoch's layout with a different `ShardUId` version and a different ancestry relationship. A shard that was cached as `false` (not a descendant of any configured tracked shard) in the pre-resharding epoch is returned as `false` again in the post-resharding epoch, even though the shard is now a child of a tracked parent. The node therefore silently stops tracking the child shard, misses state updates, and may GC state it is supposed to retain.

---

### Finding Description

`ShardTracker` holds two caches:

```
tracked_accounts_shard_cache: Arc<SyncLruCache<EpochId, BitMask>>   // keyed by EpochId ✓
descendant_of_tracked_shard_cache: Arc<Mutex<HashMap<ShardId, bool>>> // keyed by ShardId only ✗
``` [1](#0-0) 

The first cache is epoch-aware and correct. The second is not.

`check_if_descendant_of_tracked_shard` receives an `epoch_id` but ignores it during the cache lookup:

```rust
pub fn check_if_descendant_of_tracked_shard(
    &self,
    shard_id: ShardId,
    tracked_shards: &Vec<ShardUId>,
    epoch_id: &EpochId,          // ← passed in but not used for cache key
) -> Result<bool, EpochError> {
    if let Some(is_tracked) = self.descendant_of_tracked_shard_cache.lock().get(&shard_id) {
        return Ok(*is_tracked);  // ← returns cached value regardless of epoch
    }
    ...
    self.descendant_of_tracked_shard_cache.lock().insert(shard_id, is_tracked);
    Ok(is_tracked)
}
``` [2](#0-1) 

The underlying implementation `check_if_descendant_of_tracked_shard_impl` correctly uses `epoch_id` to resolve the shard layout and walk the ancestry chain using `ShardUId` (which encodes the layout version): [3](#0-2) 

The cache stores `ShardId → bool` permanently. There is no invalidation path anywhere in the codebase.

**Concrete divergence with V1 static resharding (mainnet protocol 48 → 70):**

- Old layout (version 1): shards `{0,1,2,3}`. Node configured with `TrackedShardsConfig::Shards([ShardUId{shard_id:2, version:1}])`.
- During normal operation in the old epoch, `tracks_shard_at_epoch(3, old_epoch_id)` is called. `check_if_descendant_of_tracked_shard_impl` correctly determines shard 3 (old, `{shard_id:3, version:1}`) is **not** a descendant of tracked shard 2. Result `false` is stored: `cache[ShardId(3)] = false`.
- New layout (version 3, protocol 70): shard 2 splits; new shard 3 (`{shard_id:3, version:3}`) is a **child** of old shard 2.
- `tracks_shard_at_epoch(3, new_epoch_id)` is called. The cache hit at line 539 returns `false` immediately — `epoch_id` is never consulted. The correct answer is `true`.

The same pattern applies to dynamic resharding (V3) if a child shard happens to receive a `ShardId` that was previously queried in an earlier epoch as a non-descendant.

`tracks_shard_at_epoch` is the single source of truth for `TrackedShardsConfig::Shards` and feeds every downstream decision: [4](#0-3) 

---

### Impact Explanation

All downstream consumers of `tracks_shard_at_epoch` receive the wrong answer for the affected child shard:

- **`should_apply_chunk`** — the node skips applying chunks for the child shard, so its local state diverges from the canonical chain state.
- **`get_shards_to_state_sync`** / `should_catch_up_shard` — the node does not initiate state sync for the child shard at the epoch boundary, so it never acquires the child's state.
- **`gc_state`** — the GC pass sees the shard as untracked and deletes its trie state, causing permanent data loss for an archival node. [5](#0-4) [6](#0-5) 

An archival node configured with `TrackedShardsConfig::Shards` silently loses coverage of child shards after any resharding that reuses a `ShardId` that was previously cached as `false`. The node continues to serve RPC queries as if it tracks those shards, but its answers are wrong or missing.

---

### Likelihood Explanation

`TrackedShardsConfig::Shards` is the standard configuration for archival nodes that track a subset of shards. The V1 → V2 static resharding transition (mainnet protocol 48 → 70) reuses `ShardId` values across layout versions: old shard 3 (`version:1`) and new shard 3 (`version:3`) share the same `ShardId(3)` but have different ancestry. Any archival node that was live through that transition and had queried shard 3 in the old epoch would have populated the cache with `false`, and would then silently fail to track new shard 3 as a child of old shard 2.

Dynamic resharding (V3) allocates monotonically increasing `ShardId`s (`max+1`, `max+2`), so the collision is less likely there — but the cache is still unbounded and epoch-unaware, leaving the door open for any future layout that reuses an ID.

---

### Recommendation

Change the cache key from `ShardId` to `(ShardId, EpochId)` (or equivalently `ShardUId`, which already encodes the layout version):

```rust
// Before
descendant_of_tracked_shard_cache: Arc<Mutex<HashMap<ShardId, bool>>>,

// After
descendant_of_tracked_shard_cache: Arc<Mutex<HashMap<(ShardId, EpochId), bool>>>,
```

Update the lookup and insert in `check_if_descendant_of_tracked_shard` to use `(shard_id, *epoch_id)` as the key. This makes the cache epoch-aware and eliminates the stale-value hazard across resharding boundaries while preserving the performance benefit of caching within a single epoch.

---

### Proof of Concept

```
Setup:
  Node configured with TrackedShardsConfig::Shards([ShardUId{shard_id:2, version:1}])
  Old epoch uses V1 layout version 1: shards {0,1,2,3}
  New epoch uses V1 layout version 3: shard 2 splits → children are shard 2 (v3) and shard 3 (v3)

Step 1 (old epoch):
  tracks_shard_at_epoch(ShardId(3), old_epoch_id)
    → check_if_descendant_of_tracked_shard(3, [...], old_epoch_id)
    → cache miss → impl computes false (shard 3 v1 is NOT a child of shard 2 v1)
    → cache[ShardId(3)] = false

Step 2 (resharding occurs, new epoch begins):
  tracks_shard_at_epoch(ShardId(3), new_epoch_id)
    → check_if_descendant_of_tracked_shard(3, [...], new_epoch_id)
    → cache HIT → returns false   ← WRONG
    (correct answer: true — shard 3 v3 IS a child of shard 2 v1)

Consequence:
  cares_about_shard(prev_hash, ShardId(3)) → false
  should_apply_chunk(..., ShardId(3)) → false  (chunk skipped)
  gc_state retains ShardId(3) in shards_to_cleanup → state deleted
``` [7](#0-6) [2](#0-1) [8](#0-7)

### Citations

**File:** chain/epoch-manager/src/shard_tracker.rs (L37-46)
```rust
    /// Stores a bitmask of tracked shards for each epoch ID.
    /// This cache is used to avoid recomputing the set of tracked shards.
    /// Only relevant when `TrackedShardsConfig` is set to `Accounts`.
    tracked_accounts_shard_cache: Arc<SyncLruCache<EpochId, BitMask>>,
    /// Caches whether a given shard is a descendant of any of the `tracked_shards`.
    /// This is required in scenarios with resharding, where the node must continue tracking
    /// not only the originally configured shards but also their descendants.
    /// The result is cached to avoid recomputing descendant relationships repeatedly.
    /// Only relevant when `TrackedShardsConfig` is set to `Shards(tracked_shards)`.
    descendant_of_tracked_shard_cache: Arc<Mutex<HashMap<ShardId, bool>>>,
```

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

**File:** chain/epoch-manager/src/shard_tracker.rs (L396-426)
```rust
    pub fn should_apply_chunk(
        &self,
        mode: ApplyChunksMode,
        prev_hash: &CryptoHash,
        shard_id: ShardId,
    ) -> bool {
        let cares_about_shard_this_epoch = self.cares_about_shard(prev_hash, shard_id);
        let cares_about_shard_next_epoch = self.will_care_about_shard(prev_hash, shard_id);
        let cared_about_shard_prev_epoch =
            self.cared_about_shard_in_prev_epoch_from_prev_hash(prev_hash, shard_id);
        match mode {
            // next epoch's shard states are not ready, only update this epoch's shards plus shards we will care about in the future
            // and already have state for
            ApplyChunksMode::NotCaughtUp => {
                cares_about_shard_this_epoch
                    || (cares_about_shard_next_epoch && cared_about_shard_prev_epoch)
            }
            // update both this epoch and next epoch
            ApplyChunksMode::IsCaughtUp => {
                cares_about_shard_this_epoch || cares_about_shard_next_epoch
            }
            // catching up next epoch's shard states, do not update this epoch's shard state
            // since it has already been updated through ApplyChunksMode::NotCaughtUp
            ApplyChunksMode::CatchingUp => {
                let syncing_shard = !cares_about_shard_this_epoch
                    && cares_about_shard_next_epoch
                    && !cared_about_shard_prev_epoch;
                syncing_shard
            }
        }
    }
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L533-551)
```rust
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

**File:** chain/chain/src/garbage_collection.rs (L1134-1141)
```rust
    let mut shards_to_cleanup =
        epoch_manager.get_shard_layout(block_info.epoch_id())?.shard_uids().collect_vec();

    // Remove shards that we are currently tracking from shards_to_cleanup
    shards_to_cleanup.retain(|shard_uid| {
        !shard_tracker
            .cares_about_shard_this_or_next_epoch(&latest_block_hash, shard_uid.shard_id())
    });
```

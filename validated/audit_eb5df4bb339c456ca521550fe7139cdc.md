### Title
`ancestor_uids()` tags all ancestor `ShardUId`s with the hardcoded V3 layout version instead of each ancestor's actual layout version, breaking shard-tracking descendant checks at the V2→V3 upgrade boundary — (File: `core/primitives/src/shard_layout/v3.rs`)

---

### Summary

`ShardLayoutV3::ancestor_uids()` constructs every returned `ShardUId` using the module-level constant `VERSION = 3`, regardless of the actual shard-layout version in which each ancestor shard existed. When the first V3 layout is bootstrapped from a V2 layout via `derive_with_layout_history`, the parent shard stored in `shards_ancestor_map` is a V2 shard whose real `ShardUId` carries `version: 2`. The function returns `version: 3` for it instead. The sole production consumer, `check_if_descendant_of_tracked_shard_impl`, compares these UIDs against a node's `tracked_shards` set (which holds V2 UIDs). Because `version: 3 ≠ version: 2`, the intersection is always empty, and the function incorrectly concludes the node need not track any child shard of a V2 parent — silently dropping shard-tracking responsibility at the upgrade boundary.

---

### Finding Description

**Root cause — wrong version tag in `ancestor_uids()`** [1](#0-0) [2](#0-1) 

```rust
const VERSION: ShardVersion = 3;          // line 28

pub fn ancestor_uids(&self, shard_id: ShardId) -> Vec<ShardUId> {
    self.shards_ancestor_map
        .get(&shard_id)
        .map(|ancestor_ids| ancestor_ids.iter()
            .map(|id| ShardUId::new(VERSION, *id))   // ← always version 3
            .collect())
        .unwrap_or_default()
}
```

Every ancestor shard ID is wrapped with `VERSION = 3`. But the ancestor IDs stored in `shards_ancestor_map` can originate from V2 layouts.

**How V2 ancestors end up in the V3 ancestor map**

`derive_with_layout_history` is the bootstrap path from V1/V2 to V3: [3](#0-2) 

```rust
pub fn derive_with_layout_history(
    base_shard_layout: &ShardLayout,          // a V2 layout
    new_boundary_account: AccountId,
    layout_history: &[ShardLayout],
) -> Result<Self, ShardLayoutError> {
    let shard_ids = base_shard_layout.shard_ids().collect();
    let boundary_accounts = base_shard_layout.boundary_accounts().clone();
    let shards_split_map = build_shard_split_map(layout_history);  // empty for V2 history
    Self::derive_impl(shard_ids, boundary_accounts, new_boundary_account, shards_split_map)
}
```

`build_shard_split_map` ignores layouts with `version() < 3`: [4](#0-3) 

So the returned `shards_split_map` is empty for a pure V2 history. `derive_impl` then inserts exactly one entry — the new split — whose *parent* is a V2 shard ID: [5](#0-4) 

```rust
let [last_split] = shard_ids
    .splice(new_boundary_idx..new_boundary_idx + 1, new_shards.clone())
    .collect_array()
    .expect("should only splice one shard");
shards_split_map.insert(last_split, new_shards);   // last_split is a V2 shard ID
```

`validate_and_derive_shard_ancestor_map` then builds `shards_ancestor_map` with that V2 shard ID as the ancestor of the two new V3 children: [6](#0-5) 

When `ancestor_uids()` is later called for a V3 child, it returns `ShardUId::new(3, v2_parent_id)` — a UID that has never existed in any layout.

**The broken consumer — `check_if_descendant_of_tracked_shard_impl`** [7](#0-6) 

```rust
if let Some(ancestors) = shard_layout.ancestor_uids(shard_id) {
    let ancestors = HashSet::from_iter(ancestors);
    return Ok(!ancestors.is_disjoint(&tracked_shards));
}
```

`tracked_shards` is populated from the node's configured shard set. A node that was tracking V2 shard `X` holds `ShardUId { version: 2, shard_id: X }`. `ancestor_uids()` returns `ShardUId { version: 3, shard_id: X }`. The `is_disjoint` check always returns `true` → the function returns `false` → the node silently stops tracking the V3 child shards of `X`.

**Divergent value**

| What it should be | What the code produces |
|---|---|
| `ShardUId { version: 2, shard_id: X }` | `ShardUId { version: 3, shard_id: X }` |

This is an exact, deterministic wrong value produced at every V2→V3 upgrade boundary for every node tracking specific shards.

---

### Impact Explanation

**Impact: High**

`ShardUId` is the database key for all per-shard state (flat storage, memtrie, chunk data). A node that fails the descendant check will:
- Not download or maintain state for V3 child shards it is responsible for.
- Fail to serve chunks for those shards, causing missed chunk production.
- Potentially stall the chain if enough validators are affected.

The `check_if_descendant_of_tracked_shard_impl` path is the O(1) fast path introduced specifically for V3 to replace the slower per-protocol-version iteration. It is the *only* path taken for V3 layouts: [8](#0-7) 

---

### Likelihood Explanation

**Likelihood: High**

- Triggered automatically at the first dynamic resharding event (V2→V3 transition), which is a planned production upgrade.
- Affects every node configured to track a subset of shards (non-validator archival nodes, RPC nodes, nodes with `TrackedShardsConfig`).
- No special attacker action required; the wrong UID is produced deterministically by the upgrade path itself.
- The existing tests (`derive_v3_from_history`, `build_shard_split_map_v3`) use a `to_shard_uids` helper that also produces version-3 UIDs for all ancestors, so the bug is not caught by the test suite. [9](#0-8) 

---

### Recommendation

`ancestor_uids()` must tag each ancestor `ShardId` with the version of the layout in which that ancestor actually existed, not the version of the current V3 layout.

**Option A** — store `(ShardVersion, ShardId)` pairs in `shards_ancestor_map` instead of bare `ShardId`s, populated during `validate_and_derive_shard_ancestor_map` by looking up the version from the split map or layout history.

**Option B** — add a parallel `ancestor_versions: BTreeMap<ShardId, Vec<ShardVersion>>` field to `ShardLayoutV3` and use it in `ancestor_uids()`.

Either way, the invariant to restore is:

```
ancestor_uids(child)[i].version == version of the layout in which ancestor[i] existed
```

---

### Proof of Concept

```
State:
  V2 layout (version=2): shard X → ShardUId { version: 2, shard_id: X }
  Node tracked_shards = { ShardUId { version: 2, shard_id: X } }

Upgrade:
  Dynamic resharding enabled; shard X split into Y, Z → first V3 layout created
  via derive_with_layout_history(base=V2, boundary="mid", history=[V2_layouts])

  shards_ancestor_map = { Y: [X], Z: [X] }   (X is a V2 shard ID)

Query:
  check_if_descendant_of_tracked_shard_impl(Y, tracked_shards, epoch_v3)
    → shard_layout.ancestor_uids(Y)
    → [ShardUId::new(3, X)]          ← version 3, WRONG
    → ancestors = { ShardUId { version: 3, shard_id: X } }
    → tracked_shards = { ShardUId { version: 2, shard_id: X } }
    → ancestors.is_disjoint(&tracked_shards) == true
    → returns Ok(false)              ← node does NOT track Y

Expected: Ok(true)  (node should track Y because it tracked its V2 parent X)
``` [2](#0-1) [7](#0-6) [10](#0-9)

### Citations

**File:** core/primitives/src/shard_layout/v3.rs (L28-28)
```rust
const VERSION: ShardVersion = 3;
```

**File:** core/primitives/src/shard_layout/v3.rs (L47-57)
```rust
    let mut shards_ancestor_map = ShardsAncestorMapV3::new();
    for shard_id in shard_ids {
        let mut ancestors = vec![];
        let mut current_id = *shard_id;
        while let Some(parent_shard_id) = shards_parent_map.get(&current_id) {
            ancestors.push(*parent_shard_id);
            current_id = *parent_shard_id;
        }
        shards_ancestor_map.insert(*shard_id, ancestors);
    }
    shards_ancestor_map
```

**File:** core/primitives/src/shard_layout/v3.rs (L64-73)
```rust
pub fn build_shard_split_map(layout_history: &[ShardLayout]) -> ShardsSplitMapV3 {
    let mut split_history = ShardsSplitMapV3::new();

    for window in layout_history.windows(2) {
        let current_layout = &window[0];
        let prev_layout = &window[1];

        if current_layout.version() < VERSION || prev_layout.version() < VERSION {
            break;
        }
```

**File:** core/primitives/src/shard_layout/v3.rs (L247-256)
```rust
    pub fn derive_with_layout_history(
        base_shard_layout: &ShardLayout,
        new_boundary_account: AccountId,
        layout_history: &[ShardLayout],
    ) -> Result<Self, ShardLayoutError> {
        let shard_ids = base_shard_layout.shard_ids().collect();
        let boundary_accounts = base_shard_layout.boundary_accounts().clone();
        let shards_split_map = build_shard_split_map(layout_history);
        Self::derive_impl(shard_ids, boundary_accounts, new_boundary_account, shards_split_map)
    }
```

**File:** core/primitives/src/shard_layout/v3.rs (L275-279)
```rust
        let [last_split] = shard_ids
            .splice(new_boundary_idx..new_boundary_idx + 1, new_shards.clone())
            .collect_array()
            .expect("should only splice one shard");
        shards_split_map.insert(last_split, new_shards);
```

**File:** core/primitives/src/shard_layout/v3.rs (L303-309)
```rust
    /// Get UIDs of all the shard's ancestors (parents, grandparents, etc.)
    pub fn ancestor_uids(&self, shard_id: ShardId) -> Vec<ShardUId> {
        self.shards_ancestor_map
            .get(&shard_id)
            .map(|ancestor_ids| ancestor_ids.iter().map(|id| ShardUId::new(VERSION, *id)).collect())
            .unwrap_or_default()
    }
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L581-614)
```rust
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
```

**File:** core/primitives/src/shard_layout/tests.rs (L440-443)
```rust
    assert_eq!(layout3.ancestor_uids(ShardId::new(5)), Some(to_shard_uids([1, 0])));
    assert_eq!(layout3.ancestor_uids(ShardId::new(6)), Some(to_shard_uids([1, 0])));
    assert_eq!(layout3.ancestor_uids(ShardId::new(3)), Some(to_shard_uids([2, 0])));
    assert_eq!(layout3.ancestor_uids(ShardId::new(4)), Some(to_shard_uids([2, 0])));
```

**File:** chain/epoch-manager/src/lib.rs (L794-799)
```rust
        let new_layout = next_shard_layout
            .derive_v3(boundary_account.clone(), || {
                self.get_shard_layout_history(current_protocol_version, None)
            })
            .map_err(|err| EpochError::ShardingError(err.to_string()))?;
        Ok(new_layout)
```

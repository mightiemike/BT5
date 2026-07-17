### Title
`get_split_parent_shard_uids` Stamps Parent-Shard UIDs with the Child Layout's Version, Producing Wrong DB Keys at Every Resharding Boundary — (`File: core/primitives/src/shard_layout/mod.rs`)

---

### Summary

`ShardLayout::get_split_parent_shard_uids` constructs `ShardUId` values for parent shards (which belong to the **previous** layout) using `self.version()` — the **current** layout's version. For the V1→V2 static resharding boundary (mainnet protocol version 64), this stamps every parent-shard UID with version `3` instead of the correct version `1`, producing DB keys that do not match any stored state. The wrong UIDs propagate into garbage collection, cold-storage archival, and memtrie pre-loading for resharding.

---

### Finding Description

`get_split_parent_shard_uids` is defined as:

```rust
pub fn get_split_parent_shard_uids(&self) -> BTreeSet<ShardUId> {
    let parent_shard_ids = self.get_split_parent_shard_ids();
    parent_shard_ids
        .into_iter()
        .map(|shard_id| ShardUId::new(self.version(), shard_id))
        .collect()
}
``` [1](#0-0) 

`get_split_parent_shard_ids` returns shard IDs that **no longer exist in `self`** — they are IDs from the previous layout that were retired when they split into children. [2](#0-1) 

`ShardUId` is the canonical DB key for per-shard state. Its `version` field must match the layout version under which the shard was active, so that `s1.v1` (shard 1 in V1) and `s1.v3` (shard 1 in V2/V3) are distinct keys. [3](#0-2) 

`ShardLayoutV2::new` hard-codes `VERSION = 3` regardless of how many V2 layouts have been derived: [4](#0-3) 

`ShardLayoutV1` takes its version from the caller; mainnet SimpleNightshade uses version `1`.

For the V1→V2 resharding (protocol version 64, mainnet):
- Parent shard IDs (e.g. shard `1`) live in V1 at version `1` → correct UID is `s1.v1`.
- `get_split_parent_shard_uids()` called on the V2 layout returns `ShardUId::new(3, 1)` = `s1.v3`.
- `s1.v3` does not exist in the DB; `s1.v1` is never touched.

The analog to the external report is exact: `at()` already resolves `_value`, but the caller re-indexes `self._values[checkpoint._value]`. Here, `get_split_parent_shard_ids()` already resolves the parent shard IDs, but `get_split_parent_shard_uids()` re-stamps them with the wrong layout version.

---

### Impact Explanation

The wrong UIDs propagate to three production call sites:

**1. Garbage collection** (`chain/chain/src/garbage_collection.rs`): GC uses parent-shard UIDs to identify and delete retired shard state after resharding. With the wrong UID (`s1.v3`), the lookup finds nothing; the parent shard's trie state under `s1.v1` is never deleted. Every resharding event permanently leaks the parent shard's full state into the hot DB.

**2. Cold-storage archival** (`core/store/src/archive/cold_storage.rs`): Archival nodes copy state keyed by `ShardUId` to cold storage. The wrong UID causes the parent shard's historical state to be silently omitted from cold storage, producing an incomplete archive.

**3. Memtrie pre-loading for dynamic resharding** (`chain/epoch-manager/src/adapter.rs`): `get_resharding_parent_shard_uid` calls `next_layout.get_split_parent_shard_uids()` to identify which shard's memtrie to pre-load before resharding executes. [5](#0-4) 

If the returned UID is wrong, the pre-load targets a non-existent shard. On node restart during epoch N+1 (the window between resharding decision and execution), the startup fallback uses this UID for synchronous loading; a wrong UID means the parent shard's memtrie is never loaded, and resharding fails for that node.

---

### Likelihood Explanation

The V1→V2 static resharding has already executed on mainnet (protocol version 64). Every node that ran GC or cold-storage archival after that boundary used the wrong parent-shard UIDs. Dynamic resharding (protocol version 85, `STABLE_PROTOCOL_VERSION = 86`) is now stable; the memtrie pre-loading path is live. [6](#0-5) 

For V2→V3 dynamic resharding both layouts share version `3`, so the UIDs are accidentally correct in that specific transition. The bug is deterministically triggered at any V1→V2 boundary and at any future transition where the previous layout's version differs from the current one.

---

### Recommendation

`get_split_parent_shard_uids` must use the **previous** layout's version, not `self.version()`. The function should accept the previous `ShardLayout` as a parameter and call `ShardUId::from_shard_id_and_layout(shard_id, prev_layout)` for each parent shard ID:

```rust
pub fn get_split_parent_shard_uids(
    &self,
    prev_layout: &ShardLayout,
) -> BTreeSet<ShardUId> {
    self.get_split_parent_shard_ids()
        .into_iter()
        .map(|shard_id| ShardUId::from_shard_id_and_layout(shard_id, prev_layout))
        .collect()
}
```

All three call sites (`garbage_collection.rs`, `cold_storage.rs`, `adapter.rs`) must be updated to supply the previous layout. The `get_resharding_parent_shard_uid` adapter method already has access to both `current_layout` and `next_layout` and can pass `current_layout` as `prev_layout`. [7](#0-6) 

---

### Proof of Concept

Concrete values from the mainnet V1→V2 resharding (SimpleNightshade → SimpleNightshadeV2):

- V1 layout: `version = 1`, shard IDs `[0, 1, 2, 3]`
- V2 layout: `version = 3` (hardcoded), shard IDs `[0, 1, 2, 3, 4, 5]` where shard `3` split into `{3, 4}`

Calling `v2_layout.get_split_parent_shard_uids()`:
- `get_split_parent_shard_ids()` → `{3}` (the retired V1 shard)
- `ShardUId::new(self.version()=3, shard_id=3)` → `s3.v3`

Correct value: `ShardUId::from_shard_id_and_layout(3, &v1_layout)` → `s3.v1`

DB key `s3.v3` does not exist; `s3.v1` (holding the full parent-shard trie state) is never touched by GC or cold-storage copy, and is never found by the memtrie pre-loader. [8](#0-7) [9](#0-8)

### Citations

**File:** core/primitives/src/shard_layout/mod.rs (L400-424)
```rust
    /// Returns all the shards from the previous shard layout that were
    /// split into multiple shards in this shard layout.
    pub fn get_split_parent_shard_ids(&self) -> BTreeSet<ShardId> {
        // V3 doesn't store shards which weren't split in the map, so we can return early.
        // Using explicit match to force handling a new shard layout version when it's added.
        match self {
            ShardLayout::V0(_) | ShardLayout::V1(_) | ShardLayout::V2(_) => {}
            ShardLayout::V3(v3) => return BTreeSet::from([v3.last_split]),
        }

        let mut parent_shard_ids = BTreeSet::new();
        for shard_id in self.shard_ids() {
            let parent_shard_id = self
                .try_get_parent_shard_id(shard_id)
                .expect("shard_id belongs to the shard layout");
            let Some(parent_shard_id) = parent_shard_id else {
                continue;
            };
            if parent_shard_id == shard_id {
                continue;
            }
            parent_shard_ids.insert(parent_shard_id);
        }
        parent_shard_ids
    }
```

**File:** core/primitives/src/shard_layout/mod.rs (L428-434)
```rust
    pub fn get_split_parent_shard_uids(&self) -> BTreeSet<ShardUId> {
        let parent_shard_ids = self.get_split_parent_shard_ids();
        parent_shard_ids
            .into_iter()
            .map(|shard_id| ShardUId::new(self.version(), shard_id))
            .collect()
    }
```

**File:** core/primitives/src/shard_layout/mod.rs (L457-482)
```rust
/// `ShardUId` is a unique representation for shards from different shard layouts.
///
/// Comparing to `ShardId`, which is just an ordinal number ranging from 0 to NUM_SHARDS-1,
/// `ShardUId` provides a way to unique identify shards when shard layouts may change across epochs.
/// This is important because we store states indexed by shards in our database, so we need a
/// way to unique identify shard even when shards change across epochs.
/// Another difference between `ShardUId` and `ShardId` is that `ShardUId` should only exist in
/// a node's internal state while `ShardId` can be exposed to outside APIs and used in protocol
/// level information (for example, `ShardChunkHeader` contains `ShardId` instead of `ShardUId`)
#[derive(
    BorshSerialize,
    BorshDeserialize,
    Hash,
    Clone,
    Copy,
    PartialEq,
    Eq,
    PartialOrd,
    Ord,
    ProtocolSchema,
)]
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
pub struct ShardUId {
    pub version: ShardVersion,
    pub shard_id: u32,
}
```

**File:** core/primitives/src/shard_layout/mod.rs (L522-526)
```rust
    /// Constructs a shard uid from shard id and a shard layout
    pub fn from_shard_id_and_layout(shard_id: ShardId, shard_layout: &ShardLayout) -> Self {
        assert!(shard_layout.shard_ids().any(|i| i == shard_id));
        Self::new(shard_layout.version(), shard_id)
    }
```

**File:** core/primitives/src/shard_layout/v2.rs (L203-229)
```rust
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
```

**File:** chain/epoch-manager/src/adapter.rs (L908-923)
```rust
    fn get_resharding_parent_shard_uid(
        &self,
        epoch_id: &EpochId,
        last_block_hash: &CryptoHash,
    ) -> Result<Option<ShardUId>, EpochError> {
        let next_epoch_id = self.get_next_epoch_id(last_block_hash)?;
        let current_layout = self.get_shard_layout(epoch_id)?;
        let next_layout = self.get_shard_layout(&next_epoch_id)?;
        if current_layout == next_layout {
            return Ok(None);
        }
        let split_parent_shard_uids = next_layout.get_split_parent_shard_uids();
        // There should be exactly one shard split when layout changes
        debug_assert!(split_parent_shard_uids.len() == 1);
        Ok(split_parent_shard_uids.into_iter().next())
    }
```

**File:** core/primitives-core/src/version.rs (L624-626)
```rust
/// Current protocol version used on the mainnet with all stable features.
const STABLE_PROTOCOL_VERSION: ProtocolVersion = 86;

```

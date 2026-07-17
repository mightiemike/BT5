Now I have all the information needed. The validation code at line 176 of `validate.rs` is a strict equality check — `prev_chunk_extra.proposed_split() != chunk_header.proposed_split()` — with no special-casing for new child shards. The resharding manager clones the parent's `ChunkExtra` but only updates `state_root` and `congestion_info`, leaving `proposed_split` carrying the parent's `Some(TrieSplit)` value. The child's first chunk producer computes `proposed_split = None` (cooldown blocks a new proposal), producing an unavoidable mismatch.

---

### Title
Child Shard `ChunkExtra.proposed_split` Not Reset to `None` After Dynamic Resharding Causes `InvalidChunkHeaderShardSplit` on Every Post-Resharding Chunk — (File: `chain/chain/src/resharding/manager.rs`)

---

### Summary

In `process_memtrie_resharding_storage_update()`, when child `ChunkExtra` records are created by cloning the parent's `ChunkExtra`, only `state_root` and `congestion_info` are updated. The `proposed_split` field — which holds the `TrieSplit` that triggered the resharding — is never cleared. Because chunk-header validation enforces strict equality between `prev_chunk_extra.proposed_split()` and `chunk_header.proposed_split()`, every first chunk produced for a child shard after a dynamic resharding will be rejected with `InvalidChunkHeaderShardSplit`, halting the network.

---

### Finding Description

**Root cause — missing field reset in `process_memtrie_resharding_storage_update`:**

```rust
// chain/chain/src/resharding/manager.rs  lines 258-260
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
// proposed_split is NOT reset — inherits Some(TrieSplit) from parent
```

The parent shard's last chunk must have had `proposed_split = Some(TrieSplit)` — that value is what caused `get_upcoming_shard_split()` to embed a `shard_split` in the epoch-N last-block header, which in turn drove `finalize_epoch()` to derive the new `ShardLayoutV3` for epoch N+2. Cloning the parent's `ChunkExtraV5` therefore copies that `Some(TrieSplit)` verbatim into both child `ChunkExtra` records.

**Strict equality validation in `validate_chunk_with_chunk_extra_and_receipts_root`:**

```rust
// chain/chain/src/validate.rs  lines 176-185
if prev_chunk_extra.proposed_split() != chunk_header.proposed_split() {
    DYNAMIC_RESHARDING_VALIDATION_FAILURES.with_label_values(&["chunk_header"]).inc();
    return Err(Error::InvalidChunkHeaderShardSplit(format!(
        "header has {:?}, expected {:?} ...",
        chunk_header.proposed_split(),
        prev_chunk_extra.proposed_split(),
    )));
}
```

There is no exemption for the first chunk of a newly created shard.

**Call path for the child's first chunk (epoch N+2):**

1. `chain.rs:3450` — `get_chunk_extra(prev_hash, &child_shard_uid)` returns the child's `ChunkExtra` written during resharding; `proposed_split = Some(TrieSplit)`.
2. `chain.rs:3455` — `validate_chunk_with_chunk_extra(…, prev_chunk_extra, …, chunk_header)` is called unconditionally for every new chunk.
3. The child's chunk producer calls `compute_proposed_split`, which returns `None` because `can_reshard()` fails (cooldown: `epoch_height - last_resharding < min_epochs_between_resharding`). The chunk header therefore carries `proposed_split = None`.
4. Validation: `Some(TrieSplit) ≠ None` → `InvalidChunkHeaderShardSplit`.

The `next_for_old_chunk` helper used to propagate `ChunkExtra` across blocks where a chunk is missing also does not clear `proposed_split`:

```rust
// core/primitives/src/types.rs  lines 983-987
pub fn next_for_old_chunk(&self, state_root: StateRoot) -> Self {
    let mut new_extra = self.clone();
    *new_extra.state_root_mut() = state_root;
    new_extra   // proposed_split still inherited
}
```

So even if the child shard's `ChunkExtra` is propagated through intermediate blocks, the stale `proposed_split` persists until the child's first actual chunk is produced.

The codebase itself acknowledges the incomplete field initialization with a TODO at the exact site:

```rust
// chain/chain/src/resharding/manager.rs  lines 255-257
// TODO(resharding): set all fields of `ChunkExtra`. Consider stronger
// typing. Clarify where it should happen when `State` and
// `FlatState` update is implemented.
```

The architecture doc also notes the consequence:
> `min_epochs_between_resharding` must be `> 0`: allowing back-to-back reshardings is unsafe because a freshly-created child shard would inherit `proposed_split` from the parent's final chunk while its own first chunk freshly computes `proposed_split = None`, triggering `InvalidChunkHeaderShardSplit`.

However, the cooldown only prevents the child from *proposing* a new split; it does not clear the stale `proposed_split` already stored in the child's `ChunkExtra`. The mismatch occurs regardless of the cooldown value.

---

### Impact Explanation

Every dynamic resharding event produces two child shards whose `ChunkExtra.proposed_split` is `Some(TrieSplit)`. The first chunk produced for each child shard will be rejected by every validating node with `InvalidChunkHeaderShardSplit`. No chunk endorsements are issued; the block containing those chunks cannot be finalized. The network halts at the first block of epoch N+2 after any dynamic resharding.

---

### Likelihood Explanation

Dynamic resharding was stabilized in protocol version 2.13.0 (CHANGELOG entry: "Stabilized dynamic resharding"). The feature is gated by `ProtocolFeature::DynamicResharding` and activates automatically when a shard's trie memory usage exceeds `memory_usage_threshold`. No privileged action is required to trigger it — the threshold is crossed by ordinary user activity growing shard state. Once the threshold is crossed and the cooldown has elapsed, the resharding is scheduled deterministically by the protocol. The bug fires on the very first block of the post-resharding epoch.

---

### Recommendation

In `process_memtrie_resharding_storage_update`, after cloning the parent `ChunkExtra`, explicitly clear `proposed_split` on the child record:

```rust
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
// Add: clear the inherited proposed_split — child shards start fresh
if let ChunkExtra::V5(ref mut v5) = child_chunk_extra {
    v5.proposed_split = None;
}
```

Equivalently, expose a `proposed_split_mut()` accessor on `ChunkExtra` (parallel to `congestion_info_mut`) and use it here. The same fix should be applied to `next_for_old_chunk` if it is ever used to propagate a child shard's `ChunkExtra` across blocks in the epoch before the child's first chunk.

---

### Proof of Concept

1. Deploy a test network with `ProtocolFeature::DynamicResharding` enabled and `memory_usage_threshold` set low enough to trigger a split within a few epochs.
2. Run the network until `compute_proposed_split` returns `Some(TrieSplit)` for a shard and the value is embedded in a chunk header and then in the last-block `shard_split` field.
3. Allow `finalize_epoch` to derive the new `ShardLayoutV3` for epoch N+2.
4. Observe that `process_memtrie_resharding_storage_update` writes child `ChunkExtra` records with `proposed_split = Some(TrieSplit)` (add a debug assertion or log).
5. Advance to epoch N+2. The first chunk produced for either child shard will have `proposed_split = None` in its header (cooldown blocks a new proposal).
6. `validate_chunk_with_chunk_
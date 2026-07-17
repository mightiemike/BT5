### Title
`get_outgoing_receipts_for_shard_from_store` Performs Only One Parent-Shard Lookup Across Multiple Resharding Events, Returning Wrong Receipts - (File: `chain/chain/src/store/mod.rs`)

### Summary

`get_outgoing_receipts_for_shard_from_store` captures the current shard layout once and performs exactly one `get_parent_shard_id` call when a layout mismatch is detected. When two or more resharding events have occurred between `last_included_height` and `prev_block_hash` — a scenario now reachable under `ProtocolFeature::DynamicResharding` with `min_epochs_between_resharding = 1` — the function resolves the wrong historical shard ID and returns incorrect (or empty) outgoing receipts. The code itself acknowledges this limitation in a comment referencing GitHub issue #4877.

### Finding Description

The function walks backwards through block headers to find the block at `last_included_height`, then compares the current shard layout against the layout at that block:

```rust
let shard_layout = epoch_manager.get_shard_layout_from_prev_block(&prev_block_hash)?;
// ...
let receipts_shard_layout = epoch_manager.get_shard_layout(block_header.epoch_id())?;

let receipts_shard_id = if shard_layout != receipts_shard_layout {
    shard_layout.get_parent_shard_id(shard_id)?   // ← only one hop up
} else {
    shard_id
};
``` [1](#0-0) 

The single `get_parent_shard_id` call resolves the immediate parent of `shard_id` in the current layout. If two resharding events occurred between the two epochs (e.g., shard A → {A1, A2} in epoch N+1, then A1 → {A1a, A1b} in epoch N+2), and the last included chunk was in epoch N:

- `shard_id` = A1a (epoch N+2 layout)
- `shard_layout.get_parent_shard_id(A1a)` = A1 (epoch N+1 layout)
- But receipts are stored under A (epoch N layout)

The function then fetches receipts under A1 — the wrong shard — and passes them to `reassign_outgoing_receipts_for_resharding` with an incorrect `receipts_shard_id`, producing wrong or empty receipts.

The code comment explicitly acknowledges this:

> "Note, the current way of implementation assumes that at least one chunk is generated before shard layout are changed twice. This is not a problem right now because we are changing shard layout for the first time for simple nightshade and generally not a problem if shard layout changes very rarely. But we need to implement a more theoretically correct algorithm if shard layouts will change more often in the future" [2](#0-1) 

The correct multi-hop pattern already exists in `get_incoming_receipts_for_shard`, which iteratively updates both `current_shard_id` and `current_shard_layout` at each epoch boundary crossing:

```rust
if prev_shard_layout != current_shard_layout {
    let parent_shard_id = current_shard_layout.get_parent_shard_id(current_shard_id)?;
    current_shard_id = parent_shard_id;
    current_shard_layout = prev_shard_layout;
}
``` [3](#0-2) 

`get_outgoing_receipts_for_shard_from_store` lacks this iterative update entirely.

### Impact Explanation

`get_outgoing_receipts_for_shard_from_store` is called from `validate_chunk_with_chunk_extra`, which computes the outgoing receipts Merkle root and compares it against the value committed in the chunk header: [4](#0-3) 

If the wrong receipts are returned, the computed `outgoing_receipts_root` will not match the value in `prev_chunk_extra`, causing chunk validation to fail for every chunk produced after two consecutive resharding events when a shard has missing chunks spanning both epoch boundaries. This can halt chain progress for affected shards.

### Likelihood Explanation

With `ProtocolFeature::DynamicResharding` enabled and `min_epochs_between_resharding = 1`, two consecutive resharding events are protocol-permitted. Missing chunks spanning two epoch boundaries occur when a chunk producer is offline or the network is degraded. The combination is reachable in production without any privileged action. The code comment and open issue #4877 confirm the team is aware the assumption will eventually be violated.

### Recommendation

Replace the single `get_parent_shard_id` call with an iterative walk that mirrors `get_incoming_receipts_for_shard`: maintain a `current_shard_id` and `current_shard_layout` variable, and at each epoch boundary encountered while walking backwards, call `current_shard_layout.get_parent_shard_id(current_shard_id)` and update both variables, stopping when `current_shard_layout == receipts_shard_layout`.

### Proof of Concept

1. Enable `ProtocolFeature::DynamicResharding` with `min_epochs_between_resharding = 1`.
2. Configure two forced shard splits on consecutive epochs (e.g., `force_split_shards = [shard_A, shard_A1]`).
3. Arrange for shard A1a to produce no new chunks for two epochs (simulating a missing-chunk scenario spanning both resharding boundaries).
4. Call `get_outgoing_receipts_for_shard_from_store` with `shard_id = A1a` and `last_included_height` pointing to a block in epoch N (before both splits).
5. Observe that `receipts_shard_id` resolves to A1 (one hop up) rather than A (two hops up), causing the receipt lookup to return empty or wrong data, and `validate_chunk_with_chunk_extra` to fail with an outgoing receipts root mismatch. [5](#0-4)

### Citations

**File:** chain/chain/src/store/mod.rs (L385-391)
```rust
    /// Note, the current way of implementation assumes that at least one chunk is generated before
    /// shard layout are changed twice. This is not a problem right now because we are changing shard
    /// layout for the first time for simple nightshade and generally not a problem if shard layout
    /// changes very rarely.
    /// But we need to implement a more theoretically correct algorithm if shard layouts will change
    /// more often in the future
    /// <https://github.com/near/nearcore/issues/4877>
```

**File:** chain/chain/src/store/mod.rs (L392-434)
```rust
    pub fn get_outgoing_receipts_for_shard_from_store(
        chain_store: &ChainStoreAdapter,
        epoch_manager: &dyn EpochManagerAdapter,
        prev_block_hash: CryptoHash,
        shard_id: ShardId,
        last_included_height: BlockHeight,
    ) -> Result<Vec<Receipt>, Error> {
        let shard_layout = epoch_manager.get_shard_layout_from_prev_block(&prev_block_hash)?;
        let mut receipts_block_hash = prev_block_hash;
        loop {
            let block_header = chain_store.get_block_header(&receipts_block_hash)?;

            if block_header.height() != last_included_height {
                receipts_block_hash = *block_header.prev_hash();
                continue;
            }
            let receipts_shard_layout = epoch_manager.get_shard_layout(block_header.epoch_id())?;

            // get the shard from which the outgoing receipt were generated
            let receipts_shard_id = if shard_layout != receipts_shard_layout {
                shard_layout.get_parent_shard_id(shard_id)?
            } else {
                shard_id
            };

            let mut receipts = chain_store
                .get_outgoing_receipts(&receipts_block_hash, receipts_shard_id)
                .map(|v| v.to_vec())
                .unwrap_or_default();

            if shard_layout != receipts_shard_layout {
                // the shard layout has changed so we need to reassign the outgoing receipts
                Self::reassign_outgoing_receipts_for_resharding(
                    &mut receipts,
                    &shard_layout,
                    shard_id,
                    receipts_shard_id,
                )?;
            }

            return Ok(receipts);
        }
    }
```

**File:** chain/chain/src/store/utils.rs (L218-229)
```rust
        if prev_shard_layout != current_shard_layout {
            let parent_shard_id = current_shard_layout.get_parent_shard_id(current_shard_id)?;
            tracing::info!(
                target: "chain",
                version = current_shard_layout.version(),
                prev_version = prev_shard_layout.version(),
                ?current_shard_id,
                ?parent_shard_id,
                "crossing epoch boundary with shard layout change, updating shard id"
            );
            current_shard_id = parent_shard_id;
            current_shard_layout = prev_shard_layout;
```

**File:** chain/chain/src/validate.rs (L78-94)
```rust
    let outgoing_receipts = chain_store.get_outgoing_receipts_for_shard(
        epoch_manager,
        *prev_block_hash,
        chunk_header.shard_id(),
        prev_chunk_height_included,
    )?;
    let outgoing_receipts_hashes = {
        let shard_layout = epoch_manager.get_shard_layout_from_prev_block(prev_block_hash)?;
        Chain::build_receipts_hashes(&outgoing_receipts, &shard_layout)?
    };
    let (outgoing_receipts_root, _) = merklize(&outgoing_receipts_hashes);

    validate_chunk_with_chunk_extra_and_receipts_root(
        prev_chunk_extra,
        chunk_header,
        &outgoing_receipts_root,
    )?;
```

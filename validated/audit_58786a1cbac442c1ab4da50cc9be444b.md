### Title
`ShardLayout::resolve_to_current_shard` for V0/V1/V2 layouts resolves only one generation of shard splits, causing `GlobalContractDistribution` receipts delayed across two resharding events to panic the node — (`core/primitives/src/shard_layout/mod.rs`)

---

### Summary

`ShardLayout::resolve_to_current_shard` for `V0`/`V1`/`V2` layouts delegates to `get_children_shards_ids(shard_id).map(|c| c[0])`, which only knows about the **most recent** shard split. A `GlobalContractDistribution` receipt whose `target_shard` is a grandparent shard (split two or more generations ago) causes `resolve_to_current_shard` to return `None`, which propagates as a `ShardingError` and panics the node. The V3 layout (dynamic resharding) correctly handles this by maintaining a full cumulative split history and recursively resolving through all generations.

---

### Finding Description

The analog to the ZetaChain bug is structural: ZetaChain always routes through an intermediate token (WETH) even when the input IS that token, making the path degenerate. In nearcore, `resolve_to_current_shard` for V0/V1/V2 always applies a **single-generation** resolution step, making the path degenerate when the input shard is from two or more generations ago.

**Root cause — `core/primitives/src/shard_layout/mod.rs`:**

```rust
pub fn resolve_to_current_shard(&self, shard_id: ShardId) -> Option<ShardId> {
    match self {
        Self::V0(_) | Self::V1(_) | Self::V2(_) => {
            self.get_children_shards_ids(shard_id).map(|c| c[0])  // single-generation only
        }
        Self::V3(v3) => v3.resolve_to_current_shard(shard_id),    // full history
    }
}
``` [1](#0-0) 

For V1/V2, `get_children_shards_ids` only returns children of the **most recent** split parent. For any other shard ID — including a grandparent shard from an earlier split — it returns `None`:

```rust
// V1/V2: only the most recent split is recorded
Self::V0(_) => None,
Self::V1(v1) => v1.get_children_shards_ids(parent_shard_id),
Self::V2(v2) => v2.get_children_shards_ids(parent_shard_id),
``` [2](#0-1) 

**Call path — `core/primitives/src/receipt.rs`:**

`Receipt::receiver_shard_id` calls `resolve_to_current_shard` for `GlobalContractDistribution` receipts when `target_shard` is not in the current layout:

```rust
ReceiptEnum::GlobalContractDistribution(receipt) => {
    let target_shard = receipt.target_shard();
    if shard_layout.shard_ids().contains(&target_shard) {
        target_shard
    } else {
        // The target shard may be from an arbitrarily old layout ...
        let Some(current_shard) = shard_layout.resolve_to_current_shard(target_shard)
        else {
            return Err(EpochError::ShardingError(format!(
                "Shard {target_shard} does not exist in the shard layout or its split history",
            )));
        };
        current_shard
    }
}
``` [3](#0-2) 

When `target_shard` is a grandparent shard (split two generations ago under static V1/V2 resharding), `resolve_to_current_shard` returns `None`, the `else` branch fires, and the node panics in `receipt_filter_fn()`.

**V3 correctly handles this** by checking `self.shard_ids.contains(&shard_id)` first (identity case) and then recursively following `shards_split_map` through all generations:

```rust
pub fn resolve_to_current_shard(&self, shard_id: ShardId) -> Option<ShardId> {
    if self.shard_ids.contains(&shard_id) {
        return Some(shard_id);
    }
    let children = self.shards_split_map.get(&shard_id)?;
    self.resolve_to_current_shard(children[0])
}
``` [4](#0-3) 

**The test explicitly acknowledges the gap:**

```rust
// The fix only works with V3 shard layouts (dynamic resharding).
// With static resharding, the shard layout doesn't maintain a full split history.
if !ProtocolFeature::DynamicResharding.enabled(PROTOCOL_VERSION) {
    return;
}
// ...
// If the vulnerability exists, processing the stale GlobalContractDistribution
// receipt will panic in receipt_filter_fn() when receiver_shard_id() fails
// to remap the old target_shard after two resharding generations.
``` [5](#0-4) [6](#0-5) 

The test skips entirely when `DynamicResharding` is not enabled, leaving the V1/V2 code path untested and the panic reachable.

---

### Impact Explanation

Any unprivileged user can deploy a global contract, creating a `GlobalContractDistribution` receipt with `target_shard` set to their current shard. If that receipt enters the delayed queue and the network undergoes two static resharding events (protocol version upgrades) before the receipt is processed, `receiver_shard_id` returns `Err(ShardingError(...))`, which propagates as a node panic in `receipt_filter_fn`. A panicking chunk producer stalls block production for that shard, causing a chain halt. The impact is **Critical** — chain liveness failure triggered by a normal user action combined with normal protocol upgrade operations.

---

### Likelihood Explanation

The trigger requires:
1. `GlobalContractDistribution` is enabled (protocol feature).
2. A receipt enters the delayed queue (e.g., shard is compute-saturated).
3. Two static resharding events (protocol version upgrades) occur before the receipt drains.

Static resharding events are infrequent (months apart on mainnet), so the window is narrow. However, the code path is unconditionally reachable whenever `DynamicResharding` is **not** enabled and `GlobalContractDistribution` is enabled — a combination that exists in the current codebase. The test explicitly skips rather than asserting safety, confirming the gap is known and unmitigated for V1/V2 layouts.

---

### Recommendation

For V0/V1/V2 layouts, `resolve_to_current_shard` must recursively resolve through all generations by iterating through the layout history (analogous to `check_if_descendant_of_tracked_shard_impl` in `chain/epoch-manager/src/shard_tracker.rs`, which already does this via `get_shard_layout_history` + `windows(2)`):

```rust
Self::V0(_) | Self::V1(_) | Self::V2(_) => {
    // Walk the layout history to resolve multi-generation splits,
    // mirroring the logic in check_if_descendant_of_tracked_shard_impl.
    // Return Some(shard_id) if shard_id is already current.
    if self.shard_ids().contains(&shard_id) {
        return Some(shard_id);
    }
    self.get_children_shards_ids(shard_id).map(|c| c[0])
}
```

The identity check (`shard_ids().contains`) is the minimal fix for the one-generation case; full multi-generation resolution requires threading the layout history through the call, as V3 does via its cumulative `shards_split_map`.

---

### Proof of Concept

**Step 1.** Enable `GlobalContractDistribution` but not `DynamicResharding` (static V1/V2 resharding).

**Step 2.** Deploy a global contract from account `user0` on shard `S0`. This creates a `GlobalContractDistributionReceipt` with `target_shard = S0`.

**Step 3.** Saturate shard `S0`'s compute so the receipt enters the delayed queue.

**Step 4.** Trigger the first static resharding: `S0` splits into `S1` and `S2`. The V1/V2 layout records only this split.

**Step 5.** Trigger the second static resharding: `S1` splits into `S3` and `S4`. The new V1/V2 layout records only this split; `S0` is now a grandparent with no entry in `get_children_shards_ids`.

**Step 6.** Let the delayed queue drain. When the runtime calls `receipt.receiver_shard_id(&current_layout)`:
- `current_layout.shard_ids().contains(&S0)` → `false` (S0 is retired)
- `current_layout.resolve_to_current_shard(S0)` → `get_children_shards_ids(S0)` → `None` (V1/V2 only knows about the S1→{S3,S4} split)
- Returns `Err(ShardingError("Shard S0 does not exist in the shard layout or its split history"))`
- Node panics in `receipt_filter_fn`, stalling the chain. [1](#0-0) [3](#0-2) [7](#0-6) [8](#0-7)

### Citations

**File:** core/primitives/src/shard_layout/mod.rs (L230-237)
```rust
    pub fn resolve_to_current_shard(&self, shard_id: ShardId) -> Option<ShardId> {
        match self {
            Self::V0(_) | Self::V1(_) | Self::V2(_) => {
                self.get_children_shards_ids(shard_id).map(|c| c[0])
            }
            Self::V3(v3) => v3.resolve_to_current_shard(shard_id),
        }
    }
```

**File:** core/primitives/src/shard_layout/mod.rs (L241-248)
```rust
    pub fn get_children_shards_ids(&self, parent_shard_id: ShardId) -> Option<Vec<ShardId>> {
        match self {
            Self::V0(_) => None,
            Self::V1(v1) => v1.get_children_shards_ids(parent_shard_id),
            Self::V2(v2) => v2.get_children_shards_ids(parent_shard_id),
            Self::V3(v3) => v3.get_children_shards_ids(parent_shard_id),
        }
    }
```

**File:** core/primitives/src/receipt.rs (L447-463)
```rust
            ReceiptEnum::GlobalContractDistribution(receipt) => {
                let target_shard = receipt.target_shard();
                if shard_layout.shard_ids().contains(&target_shard) {
                    target_shard
                } else {
                    // The target shard may be from an arbitrarily old layout (the receipt could
                    // have been delayed across multiple resharding events). resolve_to_current_shard
                    // will find a shard descendant in the current layout.
                    let Some(current_shard) = shard_layout.resolve_to_current_shard(target_shard)
                    else {
                        return Err(EpochError::ShardingError(format!(
                            "Shard {target_shard} does not exist in the shard layout or its split history",
                        )));
                    };
                    current_shard
                }
            }
```

**File:** core/primitives/src/shard_layout/v3.rs (L320-326)
```rust
    pub fn resolve_to_current_shard(&self, shard_id: ShardId) -> Option<ShardId> {
        if self.shard_ids.contains(&shard_id) {
            return Some(shard_id);
        }
        let children = self.shards_split_map.get(&shard_id)?;
        self.resolve_to_current_shard(children[0])
    }
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L32-39)
```rust
fn test_stale_global_contract_distribution_after_double_resharding() {
    init_test_logger();

    // The fix only works with V3 shard layouts (dynamic resharding).
    // With static resharding, the shard layout doesn't maintain a full split history.
    if !ProtocolFeature::DynamicResharding.enabled(PROTOCOL_VERSION) {
        return;
    }
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L163-186)
```rust
    assert!(both_splits_done, "both shard splits did not complete within the allotted blocks");

    // Step 4: Stop saturating. Let the delayed queue drain.
    // If the vulnerability exists, processing the stale GlobalContractDistribution
    // receipt will panic in receipt_filter_fn() when receiver_shard_id() fails
    // to remap the old target_shard after two resharding generations.
    let current_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };
    let drain_end = current_height + epoch_length * 2;
    env.runner_for_account(&chunk_producer).run_until_head_height(drain_end);

    let head_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };
    assert!(
        head_height >= drain_end,
        "chain stalled at height {}; expected >= {} (likely panicked processing stale receipt)",
        head_height,
        drain_end
    );
}
```

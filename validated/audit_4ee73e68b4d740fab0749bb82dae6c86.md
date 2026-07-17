### Title
`PeerChainInfoV2::tracked_shards` Advertises Empty Set for Non-`AllShards` Configurations Despite Active Shard Tracking — (`chain/client/src/client.rs`)

### Summary

`send_network_chain_info()` unconditionally broadcasts `tracked_shards = []` in the network handshake for every node whose `TrackedShardsConfig` is not `AllShards`. This means nodes configured with `Shards(...)`, `Accounts(...)`, `Schedule(...)`, or `ShadowValidator(...)` advertise zero tracked shards to all peers, even though they actively track specific shards. Peers store this false capability and use it for shard-specific routing decisions, making these nodes permanently invisible as routing targets for chunk requests and state-sync.

### Finding Description

In `send_network_chain_info()`, the `tracked_shards` vector sent to the network layer is computed as:

```rust
let tracked_shards = if self.config.tracked_shards_config.tracks_all_shards() {
    self.epoch_manager.shard_ids(&tip.epoch_id)?
} else {
    // TODO(cloud_archival): Revisit this to determine if improvements can be made
    // and if the issue described above has been resolved.
    vec![]
};
```

This value is then stored in `ChainInfo` and loaded by `send_handshake()`:

```rust
let (height, tracked_shards) =
    if let Some(chain_info) = self.network_state.chain_info.load().as_ref() {
        (chain_info.block.header().height(), chain_info.tracked_shards.clone())
    } else {
        (0, vec![])
    };
```

The resulting `PeerChainInfoV2 { tracked_shards, .. }` is transmitted over the wire and stored by the receiving peer as `connection.tracked_shards`. Peers then use this field for routing:

```rust
// chain/jsonrpc/src/sharded_rpc.rs
for node in &self.nodes {
    if node.tracked_shards.contains(&shard_id) {
        result.push(RpcNodeHandle::RemoteNode(node.client.clone()));
    }
}
```

And in `peer_manager_actor.rs` for chunk/state-sync routing decisions.

**Exact divergent value:** A node configured with `TrackedShardsConfig::Shards([ShardUId { version: 1, shard_id: 0 }])` actually tracks shard 0 (confirmed by `ShardTracker::cares_about_shard()` returning `true`), but advertises `tracked_shards = []` in every handshake. The receiving peer stores `connection.tracked_shards = []` and will never route shard-0 requests to this node.

The `ShardTracker` correctly computes actual tracking state for all config variants:

```rust
match &self.tracked_shards_config {
    TrackedShardsConfig::NoShards => Ok(false),
    TrackedShardsConfig::AllShards => Ok(true),
    TrackedShardsConfig::Shards(tracked_shards) => {
        self.check_if_descendant_of_tracked_shard(shard_id, tracked_shards, epoch_id)
    }
    TrackedShardsConfig::Accounts(tracked_accounts) => {
        self.check_if_shard_contains_tracked_account(shard_id, tracked_accounts, epoch_id)
    }
    TrackedShardsConfig::Schedule(schedule) => {
        self.check_if_shard_is_tracked_according_to_schedule(shard_id, schedule, epoch_id)
    }
    ...
}
```

But `send_network_chain_info()` never consults `ShardTracker` for the non-`AllShards` case — it hard-codes `vec![]`.

### Impact Explanation

Any node running with `TrackedShardsConfig::Shards(...)`, `Accounts(...)`, or `Schedule(...)` — which includes archival nodes tracking a subset of shards, RPC nodes with partial tracking, and nodes using the rotating schedule — advertises zero tracked shards to the entire network. Peers that rely on `tracked_shards` for routing (sharded RPC, chunk request routing, state-sync peer selection) will never select these nodes as candidates for shard-specific data, even though the nodes hold the requested state. This silently degrades data availability routing and can cause unnecessary state-sync failures or overload on `AllShards` nodes.

### Likelihood Explanation

This is triggered by any node operator using a non-`AllShards` tracking configuration, which is the recommended configuration for archival and RPC nodes tracking specific shards. The `TrackedShardsConfig::Shards(...)` and `Schedule(...)` variants are actively used in production and tested in the integration test suite. The mismatch is permanent for the lifetime of any connection established with such a node.

### Recommendation

In `send_network_chain_info()`, replace the hard-coded `vec![]` fallback with an actual query to `ShardTracker` for the current epoch's tracked shards:

```rust
let tracked_shards = {
    let shard_ids = self.epoch_manager.shard_ids(&tip.epoch_id)?;
    shard_ids.into_iter()
        .filter(|&shard_id| self.shard_tracker.cares_about_shard(&tip.last_block_hash, shard_id))
        .collect()
};
```

For `Schedule`-based configs, the advertised set should reflect the current epoch's active subset, not a static empty list.

### Proof of Concept

1. Configure a node with `TrackedShardsConfig::Shards(vec![shard_uid_0])`.
2. The node starts and calls `send_network_chain_info()`.
3. `tracks_all_shards()` returns `false` → `tracked_shards = vec![]` is stored in `ChainInfo`.
4. On handshake with any peer, `send_handshake()` reads `chain_info.tracked_shards = []` and sends `PeerChainInfoV2 { tracked_shards: [], .. }`.
5. The peer stores `connection.tracked_shards = []`.
6. A sharded RPC call for shard 0 arrives at the peer; `nodes_for_shard_in_epochs()` iterates connections and finds `[].contains(&shard_0) == false` → the node is never selected.
7. Meanwhile, `ShardTracker::cares_about_shard(&tip.last_block_hash, shard_0)` on the original node returns `true` — the node has the data but is unreachable for routing. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** chain/client/src/client.rs (L2741-2747)
```rust
        let tracked_shards = if self.config.tracked_shards_config.tracks_all_shards() {
            self.epoch_manager.shard_ids(&tip.epoch_id)?
        } else {
            // TODO(cloud_archival): Revisit this to determine if improvements can be made
            // and if the issue described above has been resolved.
            vec![]
        };
```

**File:** chain/network/src/peer/peer_actor.rs (L436-442)
```rust
    fn send_handshake(&self, spec: HandshakeSpec) {
        let (height, tracked_shards) =
            if let Some(chain_info) = self.network_state.chain_info.load().as_ref() {
                (chain_info.block.header().height(), chain_info.tracked_shards.clone())
            } else {
                (0, vec![])
            };
```

**File:** chain/network/src/peer/peer_actor.rs (L449-454)
```rust
            sender_chain_info: PeerChainInfoV2 {
                genesis_id: self.network_state.genesis_id.clone(),
                // TODO: remove `height` from PeerChainInfo
                height,
                tracked_shards,
                archival: self.network_state.config.archive,
```

**File:** chain/jsonrpc/src/sharded_rpc.rs (L340-346)
```rust
        // Check remote nodes. Their `tracked_shards` is a static config and
        // not epoch-dependent, so a single membership check is enough.
        for node in &self.nodes {
            if node.tracked_shards.contains(&shard_id) {
                result.push(RpcNodeHandle::RemoteNode(node.client.clone()));
            }
        }
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

**File:** chain/network/src/network_protocol/peer.rs (L119-128)
```rust
pub struct PeerChainInfoV2 {
    /// Chain Id and hash of genesis block.
    pub genesis_id: GenesisId,
    /// Last known chain height of the peer.
    pub height: BlockHeight,
    /// Shards that the peer is tracking.
    pub tracked_shards: Vec<ShardId>,
    /// Denote if a node is running in archival mode or not.
    pub archival: bool,
}
```

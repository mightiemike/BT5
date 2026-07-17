### Title
Nodes with non-`AllShards` tracking config always advertise empty `tracked_shards` in network handshake, breaking peer capability negotiation — (File: `chain/client/src/client.rs`)

---

### Summary

`send_network_chain_info()` in `chain/client/src/client.rs` sets `tracked_shards` to `vec![]` for every `TrackedShardsConfig` variant except `AllShards`. This empty list is stored in `ChainInfo`, propagated to `NetworkState.chain_info`, and then placed verbatim into `PeerChainInfoV2.tracked_shards` in every outbound handshake. Peers use that field to decide which shards a node can serve for chunk routing and state sync. Nodes configured with `Shards`, `Accounts`, or `Schedule` therefore permanently advertise zero tracked shards to the entire network, even though they actively track specific shards.

---

### Finding Description

In `send_network_chain_info()`:

```rust
// chain/client/src/client.rs  lines 2741-2747
let tracked_shards = if self.config.tracked_shards_config.tracks_all_shards() {
    self.epoch_manager.shard_ids(&tip.epoch_id)?
} else {
    // TODO(cloud_archival): Revisit this to determine if improvements can be made
    // and if the issue described above has been resolved.
    vec![]
};
``` [1](#0-0) 

`tracks_all_shards()` returns `true` only for `TrackedShardsConfig::AllShards`. For `Shards`, `Accounts`, `Schedule`, and `ShadowValidator` it returns `false`, so `tracked_shards` is always `vec![]`.

This value is then sent as `SetChainInfo(ChainInfo { tracked_shards, … })` to `PeerManagerActor`: [2](#0-1) 

`NetworkState::set_chain_info` stores it in `self.chain_info`: [3](#0-2) 

`send_handshake()` reads it back and places it directly into `PeerChainInfoV2.tracked_shards`: [4](#0-3) 

The receiving peer stores the advertised list verbatim on the `Connection` object: [5](#0-4) 

And that list is later projected into `HighestHeightPeerInfo.tracked_shards` used for state-sync peer selection and chunk routing: [6](#0-5) 

The wire format (`PeerChainInfoV2`) is a Borsh-serialised struct that is part of the stable network protocol: [7](#0-6) 

---

### Impact Explanation

Any node running with `TrackedShardsConfig::Shards`, `::Accounts`, or `::Schedule` — all documented, production-supported configurations — will advertise `tracked_shards: []` to every peer for the entire lifetime of every connection. Peers use this field to:

1. Select state-sync sources: a peer with `tracked_shards: []` is never chosen to serve state parts, even if it holds them.
2. Route `PartialEncodedChunkRequest` messages: peers only forward chunk requests to nodes that advertise the relevant shard.

The result is that these nodes are effectively invisible to the network for chunk serving and state sync, despite actively tracking and holding state for specific shards. If no `AllShards` node is reachable, state sync for those shards can stall entirely.

---

### Likelihood Explanation

`TrackedShardsConfig::Shards` and `TrackedShardsConfig::Schedule` are first-class, documented configurations used by RPC nodes, archival nodes, and nodes undergoing shard-schedule rotation. The condition is triggered unconditionally whenever `tracked_shards_config` is anything other than `AllShards`. No special timing or attacker action is required; the miscommunication occurs on every handshake.

---

### Recommendation

In `send_network_chain_info()`, compute the actual set of shards tracked in the current epoch for all non-`NoShards` configs, not just for `AllShards`. The existing `ShardTracker` already has the per-epoch logic (`tracks_shard_at_epoch`, `get_tracked_shards_for_non_validator_in_epoch`) that can be used to enumerate the correct shard set. At minimum, the condition should be extended to cover `TrackedShardsConfig::Shards` (analogous to the external bug's fix: check whether the alternative storage path is active before defaulting to the local field).

---

### Proof of Concept

1. Start a node with `tracked_shards_config: { "Shards": [{ "shard_id": 0, "version": 0 }] }`.
2. Connect a second node and capture the handshake bytes.
3. Decode `PeerChainInfoV2.tracked_shards` from the handshake — it will be `[]`.
4. The second node's `HighestHeightPeerInfo.tracked_shards` for the first node will be `[]`.
5. Any `PartialEncodedChunkRequest` for shard 0 will not be routed to the first node; state-sync will not select it as a source for shard 0 state parts, even though it holds them.

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

**File:** chain/client/src/client.rs (L2750-2754)
```rust
        self.network_adapter.send(SetChainInfo(ChainInfo {
            block,
            tracked_shards,
            tier1_accounts,
        }));
```

**File:** chain/network/src/peer_manager/network_state/mod.rs (L1507-1517)
```rust
    pub fn set_chain_info(
        self: &Arc<Self>,
        info: ChainInfo,
        transport: &dyn NetworkTransport,
    ) -> bool {
        let _mutex = self.set_chain_info_mutex.lock();

        // We set state.chain_info and call accounts_data.set_keys
        // synchronously, therefore, assuming actors deliver messages in order, there
        // will be no race condition between subsequent SetChainInfo calls.
        self.chain_info.store(Arc::new(Some(info.clone())));
```

**File:** chain/network/src/peer/peer_actor.rs (L437-455)
```rust
        let (height, tracked_shards) =
            if let Some(chain_info) = self.network_state.chain_info.load().as_ref() {
                (chain_info.block.header().height(), chain_info.tracked_shards.clone())
            } else {
                (0, vec![])
            };
        let handshake = Handshake {
            protocol_version: spec.protocol_version,
            oldest_supported_version: MIN_SUPPORTED_PROTOCOL_VERSION,
            sender_peer_id: self.network_state.config.node_id(),
            target_peer_id: spec.peer_id,
            sender_listen_port: self.network_state.config.node_addr.as_ref().map(|a| a.port()),
            sender_chain_info: PeerChainInfoV2 {
                genesis_id: self.network_state.genesis_id.clone(),
                // TODO: remove `height` from PeerChainInfo
                height,
                tracked_shards,
                archival: self.network_state.config.archive,
            },
```

**File:** chain/network/src/peer/peer_actor.rs (L637-638)
```rust
            tracked_shards: handshake.sender_chain_info.tracked_shards.clone(),
            archival: handshake.sender_chain_info.archival,
```

**File:** chain/network/src/peer_manager/peer_manager_actor.rs (L287-299)
```rust
fn to_highest_height_peer_info(
    peer_state: &ConnectedPeerState,
    genesis_id: &GenesisId,
) -> Option<HighestHeightPeerInfo> {
    let block = peer_state.block_info.as_ref()?;
    Some(HighestHeightPeerInfo {
        peer_info: peer_state.peer_info.clone(),
        genesis_id: genesis_id.clone(),
        highest_block_height: block.height,
        highest_block_hash: block.hash,
        tracked_shards: peer_state.tracked_shards.clone(),
        archival: peer_state.archival,
    })
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

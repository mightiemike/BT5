### Title
Unverified Self-Reported `archival` Flag in Handshake Poisons Archival Node's Safe-Set, Evicting Legitimate Peers — (`File: chain/network/src/peer_manager/peer_manager_actor.rs`)

---

### Summary

Any unprivileged peer can set `sender_chain_info.archival = true` in its `Handshake` message. The receiving archival node trusts this self-reported boolean without any verification, stores it verbatim in `ConnectedPeerState`, and then uses it to build the "safe set" of peers that are protected from disconnection. A malicious non-archival peer that lies about being archival occupies a protected slot, causing a legitimate peer to be evicted instead when the node is over capacity — a direct analog of the NFTXFeeDistributor bug where trusting a callee's return value causes another party to lose out.

---

### Finding Description

**Step 1 — Attacker-controlled data enters without verification.**

During the TCP handshake, a peer sends a `Handshake` protobuf message containing `sender_chain_info` of type `PeerChainInfo`. Field 4 of that message is `bool archival`. [1](#0-0) 

The `process_handshake` function in `peer_actor.rs` validates `protocol_version`, `genesis_id`, `sender_peer_id`, `tier`, and `nonce`, but performs **no check** on `sender_chain_info.archival`. The value is copied directly into the `Connection` struct: [2](#0-1) 

**Step 2 — The unverified value is stored in `ConnectedPeerState`.**

`on_peer_connected` propagates `info.archival` (which came from the handshake) into the canonical `ConnectedPeerState` record: [3](#0-2) 

**Step 3 — The poisoned flag drives connection-eviction decisions.**

`maybe_stop_active_connection` is called periodically whenever the node is over `ideal_connections_hi`. It builds a "safe set" of peers that must not be disconnected. For archival nodes, every peer whose stored `archival` flag is `true` is added to the safe set, provided the count of such peers is at or below `archival_peer_connections_lower_bound` (default: 10): [4](#0-3) 

A peer outside the safe set is then randomly selected and disconnected: [5](#0-4) 

**Step 4 — Exact divergent value.**

The divergent Borsh/protobuf field is `PeerChainInfo.archival` (proto field 4, Rust field `PeerChainInfoV2::archival: bool`). A malicious peer sets it to `true`; the node stores `true` and acts on it. The correct value — derivable only from the peer's own local config — is never checked. [6](#0-5) 

---

### Impact Explanation

An archival node configured with `archival_peer_connections_lower_bound = 10` (the default) will protect up to 10 fake "archival" peers from eviction. When the node is at capacity and `maybe_stop_active_connection` fires, it will evict a legitimate peer (possibly a real archival peer) instead of the malicious one. Repeated connections from multiple colluding malicious peers can fill the entire safe-set quota, guaranteeing that only non-archival or malicious peers survive rebalancing. This degrades the archival node's ability to maintain the minimum archival-peer connectivity it was configured to guarantee, and can cause legitimate peers to permanently lose their connection slots to that node.

---

### Likelihood Explanation

Any node on the NEAR network can open a TCP connection and send a handshake with `archival = true`. No stake, no validator key, and no special privilege is required. The attack is trivially repeatable: a single attacker can open up to `archival_peer_connections_lower_bound` connections (default 10) from different `PeerId`s, each claiming to be archival, and permanently occupy the safe-set quota. The `maybe_stop_active_connection` trigger fires on every `monitor_peers_trigger` cycle, so the eviction of legitimate peers is continuous.

---

### Recommendation

Do not trust the self-reported `archival` field from a peer's handshake for connection-management decisions. The `archival` flag should be treated as an informational hint only (e.g., for display in debug views), not as a security-relevant capability claim that grants safe-set protection. The safe-set archival logic in `maybe_stop_active_connection` should be removed or replaced with a mechanism that does not rely on peer-supplied data — for example, maintaining archival-peer connections only to peers whose `PeerId` appears in a locally-configured allowlist, or simply removing the archival-peer safe-set guarantee entirely and relying on the whitelist mechanism for critical peers.

---

### Proof of Concept

1. Archival node A is configured with `archive = true`, `archival_peer_connections_lower_bound = 10`, `max_num_peers = 40`, `ideal_connections_hi = 35`.
2. Attacker opens 10 TCP connections to A, each from a distinct `PeerId`, each sending a valid `Tier2Handshake` with `sender_chain_info.archival = true` and a correct `genesis_id` and `protocol_version`.
3. All 10 connections pass `process_handshake` validation and are registered with `archival = true` in `ConnectedPeerState`.
4. Node A now has `archival_count = 10 == archival_peer_connections_lower_bound`, so all 10 malicious peers enter the safe set on every `maybe_stop_active_connection` call.
5. When A reaches 36 connections (over `ideal_connections_hi = 35`), `maybe_stop_active_connection` fires. The 10 malicious peers are in the safe set; the victim is chosen from the remaining 26 legitimate peers.
6. A legitimate peer B is disconnected. B loses its connection to the archival node despite having done nothing wrong — the direct analog of the NFTXFeeDistributor receiver losing fees because a malicious receiver returned `false`.

### Citations

**File:** chain/network/src/network_protocol/network.proto (L122-131)
```text
// Basic information about the chain view maintained by a peer.
message PeerChainInfo {
  GenesisId genesis_id = 1;
  // Height of the highest NEAR chain block known to a peer.
  uint64 height = 2;
  // Shards of the NEAR chain tracked by the peer.
  repeated uint64 tracked_shards = 3;
  // Whether the peer is an archival node.
  bool archival = 4;
}
```

**File:** chain/network/src/peer/peer_actor.rs (L637-638)
```rust
            tracked_shards: handshake.sender_chain_info.tracked_shards.clone(),
            archival: handshake.sender_chain_info.archival,
```

**File:** chain/network/src/peer_manager/network_state/mod.rs (L494-506)
```rust
        self.peers.insert(
            peer_id,
            ConnectedPeerState {
                peer_info: info.peer_info,
                block_info: None,
                tier: info.tier,
                archival: info.archival,
                tracked_shards: info.tracked_shards,
                owned_account_key: account_key,
                peer_type: info.peer_type,
                established_time: info.established_time,
            },
        );
```

**File:** chain/network/src/peer_manager/peer_manager_actor.rs (L575-586)
```rust
        // If there is not enough archival peers, add them to the safe set.
        if self.state.config.archive {
            let archival_count = t2_peers.iter().filter(|(_, s)| s.archival).count();
            if archival_count <= self.state.config.archival_peer_connections_lower_bound as usize {
                let archival_ids = t2_peers
                    .iter()
                    .filter(|(_, s)| s.archival)
                    .map(|(id, _)| id.clone())
                    .collect_vec();
                safe_set.extend(archival_ids);
            }
        }
```

**File:** chain/network/src/peer_manager/peer_manager_actor.rs (L610-620)
```rust
        // Build valid candidate list: all peers outside the safe set.
        let candidates: Vec<&PeerId> =
            t2_peers.keys().filter(|id| !safe_set.contains(*id)).collect();
        if let Some(id) = candidates.choose(&mut rand::thread_rng()) {
            tracing::debug!(target: "network", ?id,
                t2_count,
                ideal_connections_hi = self.state.config.ideal_connections_hi,
                "stopping active connection"
            );
            self.transport.disconnect_peer(id, None);
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

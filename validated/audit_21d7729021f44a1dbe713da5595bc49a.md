### Title
Unchecked Return Value of `transport.send_message` in T1 Path of `send_message_to_account` Silently Drops Consensus-Critical Messages — (File: `chain/network/src/peer_manager/network_state/mod.rs`)

### Summary

`send_message_to_account` unconditionally returns `true` after calling `transport.send_message(tcp::Tier::T1, ...)` without checking its `bool` return value. This is the nearcore analog of calling `transferFrom` without checking success: the caller is told delivery succeeded when the underlying transport silently failed. For consensus-critical messages (`ChunkEndorsement`, `PartialEncodedStateWitness`, `ContractCodeResponse`), this breaks the capability-truthfulness invariant and can cause silent liveness failures.

### Finding Description

`send_message_to_account` in `chain/network/src/peer_manager/network_state/mod.rs` has two distinct layers of unchecked return values:

**Layer 1 — inside `send_message_to_account` (T1 path):**

```rust
// line 822
transport.send_message(tcp::Tier::T1, peer_id, peer_msg);
return true;   // ← bool return of send_message is discarded; always claims success
```

`transport.send_message` (implemented in `TcpTransport::send_message` → `Pool::send_message`) returns `false` when the target peer is not in the ready pool. The T1 path discards this `bool` and unconditionally returns `true`, falsely advertising that the message was delivered. [1](#0-0) 

`Pool::send_message` explicitly documents and returns `false` on failure: [2](#0-1) 

**Layer 2 — call sites in `peer_manager_actor.rs` that discard the outer `bool`:**

Several consensus-critical `NetworkRequests` variants call `send_message_to_account` and discard its return value entirely, always returning `NetworkResponses::NoResponse` regardless of routing outcome:

- `ChunkEndorsement` (lines 1247–1255)
- `PartialEncodedStateWitness` loop (lines 1279–1284)
- `PartialEncodedStateWitnessForward` loop (lines 1311–1316)
- `ContractCodeRequest` (lines 1353–1360)
- `ContractCodeResponse` (lines 1362–1369)
- `ChunkContractAccesses` loop (lines 1342–1351)
- `SpiceChunkEndorsement` (lines 1405–1412) [3](#0-2) [4](#0-3) 

Contrast with message types that **do** check the return value and propagate `RouteNotFound`: [5](#0-4) 

### Impact Explanation

**Capability truthfulness invariant broken.** `send_message_to_account` is documented as returning `bool` — "Return whether the message is sent or not." The T1 path violates this contract: it returns `true` even when `Pool::send_message` returned `false` (peer not connected). Any caller relying on this `bool` to detect routing failure receives a false positive.

**Consensus liveness impact.** `ChunkEndorsement` messages are sent by chunk validators to block producers after validating a state witness. If the T1 connection to the block producer's proxy is absent, the endorsement is silently dropped. The block producer never receives enough endorsements to include the chunk, stalling chunk production for that shard. The chunk validator has no signal that delivery failed and does not retry via T2 fallback. [6](#0-5) 

**Partial state witness distribution impact.** `PartialEncodedStateWitness` parts are sent to each chunk validator owner. Silent T1 delivery failure means a validator owner never receives its part, cannot forward it, and the witness reconstruction quorum may not be reached. [7](#0-6) 

### Likelihood Explanation

T1 connections are maintained only between validators and their proxies. During normal operation, T1 peers cycle in and out of the ready pool (reconnection, epoch transitions, proxy changes). Any window where a target validator's T1 proxy is not yet in the pool causes `Pool::send_message` to return `false`. This is a routine network condition, not an adversarial one. The false `true` return means no retry or T2 fallback is attempted. [8](#0-7) 

### Recommendation

1. **Fix the T1 path in `send_message_to_account`**: propagate the `bool` return of `transport.send_message` instead of discarding it:

```rust
// chain/network/src/peer_manager/network_state/mod.rs, line 822
let sent = transport.send_message(tcp::Tier::T1, peer_id, peer_msg);
return sent;
```

2. **Fix call sites for consensus-critical messages**: check the return value of `send_message_to_account` for `ChunkEndorsement`, `PartialEncodedStateWitness`, `PartialEncodedStateWitnessForward`, and related variants. On `false`, either return `NetworkResponses::RouteNotFound` (consistent with other message types) or implement a T2 fallback.

3. **Fix the routed-forward path in `peer_actor.rs`**: `send_message_to_peer` return value is also discarded when forwarding routed messages. [9](#0-8) 

### Proof of Concept

1. Node A (chunk validator) validates a state witness and calls `send_chunk_endorsement_to_block_producers`, which enqueues `NetworkRequests::ChunkEndorsement(block_producer, endorsement)`.
2. `PeerManagerActor` handles the request at line 1247, calling `send_message_to_account(..., T1MessageBody::VersionedChunkEndorsement(...))`.
3. Inside `send_message_to_account`, the T1 path finds a `peer_id` for the block producer's proxy but the proxy is not yet in `tier1` pool (e.g., reconnecting after epoch transition).
4. `transport.send_message(T1, peer_id, msg)` → `Pool::send_message` → peer not in `pool.ready` → returns `false`, logs "failed sending message: peer not connected".
5. `send_message_to_account` discards the `false` and returns `true`.
6. `PeerManagerActor` returns `NetworkResponses::NoResponse` — no error, no retry, no T2 fallback.
7. The block producer never receives the endorsement. If enough validators hit this condition, the chunk is not endorsed and the shard stalls. [10](#0-9) [11](#0-10) [3](#0-2)

### Citations

**File:** chain/network/src/peer_manager/network_state/mod.rs (L803-824)
```rust
        if tcp::Tier::T1.is_allowed_send_routed(&msg) {
            for key in accounts_data.keys_by_id.get(account_id).iter().flat_map(|keys| keys.iter())
            {
                let data = match accounts_data.data.get(key) {
                    Some(data) => data,
                    None => continue,
                };
                let peer_id = match self.get_tier1_proxy(data) {
                    Some(peer_id) => peer_id,
                    None => continue,
                };
                // TODO(gprusak): in case of PartialEncodedChunk, consider stripping everything
                // but the header. This will bound the message size
                let raw = RawRoutedMessage {
                    target: PeerIdOrHash::PeerId(data.peer_id.clone()),
                    body: msg,
                };
                let signed = self.sign_message(clock, raw);
                let peer_msg = Arc::new(PeerMessage::Routed(signed));
                transport.send_message(tcp::Tier::T1, peer_id, peer_msg);
                return true;
            }
```

**File:** chain/network/src/peer_manager/connection/mod.rs (L371-386)
```rust
    /// Send message to peer that belongs to our active set
    /// Return whether the message is sent or not.
    pub fn send_message(&self, peer_id: PeerId, msg: Arc<PeerMessage>) -> bool {
        let pool = self.load();
        if let Some(peer) = pool.ready.get(&peer_id) {
            peer.send_message(msg);
            return true;
        }
        tracing::debug!(target: "network",
           to = ?peer_id,
           num_connected_peers = pool.ready.len(),
           ?msg,
           "failed sending message: peer not connected"
        );
        false
    }
```

**File:** chain/network/src/peer_manager/peer_manager_actor.rs (L1187-1200)
```rust
            NetworkRequests::PartialEncodedChunkMessage { account_id, partial_encoded_chunk } => {
                if self.state.send_message_to_account(
                    &self.clock,
                    &account_id,
                    T1MessageBody::VersionedPartialEncodedChunk(Box::new(
                        partial_encoded_chunk.into(),
                    ))
                    .into(),
                    &*self.transport,
                ) {
                    NetworkResponses::NoResponse
                } else {
                    NetworkResponses::RouteNotFound
                }
```

**File:** chain/network/src/peer_manager/peer_manager_actor.rs (L1247-1255)
```rust
            NetworkRequests::ChunkEndorsement(target, endorsement) => {
                self.state.send_message_to_account(
                    &self.clock,
                    &target,
                    T1MessageBody::VersionedChunkEndorsement(endorsement).into(),
                    &*self.transport,
                );
                NetworkResponses::NoResponse
            }
```

**File:** chain/network/src/peer_manager/peer_manager_actor.rs (L1270-1286)
```rust
                for (chunk_validator, versioned_witness) in validator_witness_tuple {
                    let t1_body = match versioned_witness {
                        VersionedPartialEncodedStateWitness::V1(w) => {
                            T1MessageBody::PartialEncodedStateWitness(w)
                        }
                        v @ VersionedPartialEncodedStateWitness::V2(_) => {
                            T1MessageBody::VersionedPartialEncodedStateWitness(v)
                        }
                    };
                    self.state.send_message_to_account(
                        &self.clock,
                        &chunk_validator,
                        t1_body.into(),
                        &*self.transport,
                    );
                }
                NetworkResponses::NoResponse
```

**File:** chain/client/src/stateless_validation/chunk_validator/mod.rs (L62-72)
```rust
    let endorsement = ChunkEndorsement::new(epoch_id, chunk_header, signer);
    let mut send_to_itself = None;
    for block_producer in block_producers {
        if &block_producer == signer.validator_id() {
            send_to_itself = Some(endorsement.clone());
        }
        network_sender.send(PeerManagerMessageRequest::NetworkRequests(
            NetworkRequests::ChunkEndorsement(block_producer, endorsement.clone()),
        ));
    }
    send_to_itself
```

**File:** chain/network/src/peer/peer_actor.rs (L1350-1357)
```rust
                    RoutedAction::Forward(msg) => {
                        self.network_state.send_message_to_peer(
                            &self.clock,
                            conn.tier,
                            msg,
                            &*self.tcp,
                        );
                    }
```

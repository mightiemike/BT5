### Title
Unconstrained `epoch_height` in `SnapshotHostInfo` permanently locks a peer's cache slot with a fake snapshot advertisement, disrupting state sync - (`chain/network/src/snapshot_hosts/mod.rs`)

### Summary

Any network peer can broadcast a `SyncSnapshotHosts` message containing a `SnapshotHostInfo` with `epoch_height = u64::MAX` and an arbitrary `sync_hash`/`shards` list. The only validation performed is a signature check (proving the message came from the claimed `peer_id`) and a shard-count bound. Neither `sync_hash` nor `epoch_height` is validated against the actual chain. Once accepted, the fake entry permanently occupies that peer's slot in `SnapshotHostsCache`: the `is_new` monotonicity gate rejects all future legitimate updates from the same peer (since no real epoch height can exceed `u64::MAX`), and the epoch-retention discard mechanism can never remove it (since `min_epoch_to_keep > u64::MAX` is unreachable). Syncing nodes route state-part and state-header requests to this fake host, which never delivers valid data, stalling state sync.

### Finding Description

`SnapshotHostInfo::verify()` enforces only two invariants:

```rust
// chain/network/src/network_protocol/state_sync.rs
pub(crate) fn verify(&self) -> Result<(), SnapshotHostInfoVerificationError> {
    if self.shards.len() > MAX_SHARDS_PER_SNAPSHOT_HOST_INFO {
        return Err(SnapshotHostInfoVerificationError::TooManyShards(self.shards.len()));
    }
    if !self.signature.verify(self.hash().as_ref(), self.peer_id.public_key()) {
        return Err(SnapshotHostInfoVerificationError::InvalidSignature);
    }
    Ok(())
}
``` [1](#0-0) 

The signed payload is `hash_borsh((sync_hash, epoch_height, shards))`. The signature proves authorship by `peer_id` but says nothing about whether `sync_hash` is a real canonical block, whether `epoch_height` matches the actual chain epoch, or whether the peer holds any snapshot data. [2](#0-1) 

The `SnapshotHostsCache` uses `epoch_height` as a monotonic version number to decide whether an incoming entry supersedes the stored one:

```rust
// chain/network/src/snapshot_hosts/mod.rs
fn is_new(&self, h: &SnapshotHostInfo) -> bool {
    if self.discard_snapshot_infos_below_epoch_height
        .is_some_and(|min_epoch| min_epoch > h.epoch_height)
    {
        return false;
    }
    match self.hosts.peek(&h.peer_id) {
        Some(old) if old.epoch_height >= h.epoch_height => false,
        _ => true,
    }
}
``` [3](#0-2) 

The epoch-retention discard threshold is computed as:

```rust
let min_epoch_to_keep = epoch_height.saturating_sub(self.epoch_retention_window);
``` [4](#0-3) 

With `STATE_SNAPSHOT_INFO_RETENTION_WINDOW = 1`, `min_epoch_to_keep` is at most `u64::MAX - 1`. The condition `min_epoch_to_keep > u64::MAX` is therefore unreachable, so a fake entry with `epoch_height = u64::MAX` is **never discarded** by the retention mechanism. [5](#0-4) 

Once the fake entry is stored, `select_host_for_header` and `select_host_for_part` will route state sync requests to the malicious peer:

```rust
// peer_manager_actor.rs
let Some(peer_id) = self.state.snapshot_hosts
    .select_host_for_header(&sync_prev_prev_hash, shard_id)
else { ... };
self.state.pending_tier3_requests.insert(peer_id.clone(), self.clock.now());
``` [6](#0-5) 

The malicious peer receives the routed `StateHeaderRequest` or `StatePartRequest` and can respond with `Busy` or simply not respond, causing the syncing node to time out and retry. With multiple Sybil identities (each requiring only a key pair), an attacker can fill the bounded LRU cache and evict all legitimate snapshot hosts. [7](#0-6) 

### Impact Explanation

A syncing node that cannot find a legitimate snapshot host for a required shard cannot complete state sync and cannot join the network. The `SnapshotHostsCache` is bounded by `snapshot_hosts_cache_size`; an attacker with enough Sybil identities can fill it entirely with fake entries, evicting all real hosts. Even a single fake entry for the only known host of a shard is sufficient to stall sync for that shard. The `pending_tier3_requests` map accumulates entries for the fake peer until `PENDING_TIER3_REQUEST_TIMEOUT` expires, consuming memory and connection-slot budget.

### Likelihood Explanation

The attack requires only valid key pairs (no stake, no validator role, no privileged access). `SyncSnapshotHosts` messages are gossiped to all connected peers and re-broadcast by every intermediate node that accepts them, so a single malicious peer connected anywhere in the network can propagate fake entries globally. The `epoch_height = u64::MAX` variant requires only one message per Sybil identity and is self-sustaining (no periodic refresh needed).

### Recommendation

1. **Bound `epoch_height` to a plausible range**: Reject `SnapshotHostInfo` entries whose `epoch_height` exceeds the locally known chain epoch height by more than a small tolerance (e.g., 2 epochs) inside `SnapshotHostInfo::verify()` or at the cache-insertion layer. This requires passing the current epoch height into the verification path.

2. **Validate `sync_hash` against the local chain**: Before inserting a `SnapshotHostInfo`, check that `sync_hash` corresponds to a known canonical block (or at least a plausible recent sync hash). The chain already exposes `check_sync_hash_validity` for this purpose.

3. **Rate-limit per peer**: Limit how frequently a given `peer_id` can update its `SnapshotHostInfo` entry to prevent rapid epoch-height escalation.

### Proof of Concept

A malicious node with key pair `(sk, pk)` constructs:

```rust
let peer_id = PeerId::new(sk.public_key());
// Use the real current sync_hash so the entry matches the active sync
let sync_hash = /* current epoch sync hash */;
let epoch_height = u64::MAX;          // permanently locks the slot
let shards = vec![ShardId::new(0)];   // claim shard 0
let info = SnapshotHostInfo::new(peer_id, sync_hash, epoch_height, shards, &sk);
// Broadcast SyncSnapshotHosts { hosts: vec![Arc::new(info)] }
```

After this message propagates:

- `SnapshotHostInfo::verify()` passes (valid signature, ≤ MAX_SHARDS_PER_SNAPSHOT_HOST_INFO shards).
- `is_new` returns `true` (no prior entry, or prior entry has `epoch_height < u64::MAX`).
- The entry is stored in `SnapshotHostsCache`.
- Any subsequent legitimate update from the same peer with a real `epoch_height` (e.g., 50) is rejected by `is_new` because `u64::MAX >= 50`.
- `update_discard_epoch_threshold` never removes it because `min_epoch_to_keep > u64::MAX` is unreachable.
- Syncing nodes call `select_host_for_header(sync_hash, ShardId::new(0))` and receive this peer's `PeerId`, insert it into `pending_tier3_requests`, and send a routed `StateHeaderRequest` that the malicious peer ignores. [8](#0-7) [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** chain/network/src/network_protocol/state_sync.rs (L27-79)
```rust
pub struct SnapshotHostInfo {
    /// Id of the node serving the snapshot
    pub peer_id: PeerId,
    /// Hash of the snapshot's state root
    pub sync_hash: CryptoHash,
    /// Ordinal of the epoch of the state root
    pub epoch_height: EpochHeight,
    /// List of shards included in the snapshot.
    pub shards: Vec<ShardId>,
    /// Signature on (sync_hash, epoch_height, shards)
    pub signature: Signature,
}

impl SnapshotHostInfo {
    fn build_hash(
        sync_hash: &CryptoHash,
        epoch_height: &EpochHeight,
        shards: &Vec<ShardId>,
    ) -> CryptoHash {
        CryptoHash::hash_borsh((sync_hash, epoch_height, shards))
    }

    pub(crate) fn new(
        peer_id: PeerId,
        sync_hash: CryptoHash,
        epoch_height: EpochHeight,
        shards: Vec<ShardId>,
        secret_key: &SecretKey,
    ) -> Self {
        #[cfg(not(test))]
        assert_eq!(&secret_key.public_key(), peer_id.public_key());
        let hash = Self::build_hash(&sync_hash, &epoch_height, &shards);
        let signature = secret_key.sign(hash.as_ref());
        Self { peer_id, sync_hash, epoch_height, shards, signature }
    }

    pub(crate) fn hash(&self) -> CryptoHash {
        Self::build_hash(&self.sync_hash, &self.epoch_height, &self.shards)
    }

    pub(crate) fn verify(&self) -> Result<(), SnapshotHostInfoVerificationError> {
        // Number of shards must be limited, otherwise it'd be possible to create malicious
        // messages with millions of shard ids.
        if self.shards.len() > MAX_SHARDS_PER_SNAPSHOT_HOST_INFO {
            return Err(SnapshotHostInfoVerificationError::TooManyShards(self.shards.len()));
        }

        if !self.signature.verify(self.hash().as_ref(), self.peer_id.public_key()) {
            return Err(SnapshotHostInfoVerificationError::InvalidSignature);
        }

        Ok(())
    }
```

**File:** chain/network/src/snapshot_hosts/mod.rs (L28-29)
```rust
/// The number of older epochs to retain snapshot host infos for.
pub const STATE_SNAPSHOT_INFO_RETENTION_WINDOW: EpochHeight = 1;
```

**File:** chain/network/src/snapshot_hosts/mod.rs (L136-153)
```rust
struct Inner {
    /// The latest known SnapshotHostInfo for each node in the network
    hosts: LruCache<PeerId, Arc<SnapshotHostInfo>>,
    /// The current sync hash being actively synced by this node. Used to reset peer selectors when changed.
    /// Updated only by locally-produced sync requests.
    current_state_sync_hash: Option<CryptoHash>,
    /// Minimum epoch height to keep in the snapshot host cache. Snapshot infos below this are discarded.
    /// Updated based on chain head progression.
    discard_snapshot_infos_below_epoch_height: Option<EpochHeight>,
    /// Available hosts for the active state sync, by shard
    hosts_for_shard: HashMap<ShardId, HashSet<PeerId>>,
    /// Local data structures used to distribute state part requests among known hosts
    peer_selector: HashMap<(ShardId, u64), PartPeerSelector>,
    /// Batch size for populating the peer_selector from the hosts
    part_selection_cache_batch_size: usize,
    /// Epoch retention window
    epoch_retention_window: EpochHeight,
}
```

**File:** chain/network/src/snapshot_hosts/mod.rs (L155-168)
```rust
impl Inner {
    fn is_new(&self, h: &SnapshotHostInfo) -> bool {
        // Discard snapshot infos below the epoch height threshold set by chain progression
        if self
            .discard_snapshot_infos_below_epoch_height
            .is_some_and(|min_epoch| min_epoch > h.epoch_height)
        {
            return false;
        }
        match self.hosts.peek(&h.peer_id) {
            Some(old) if old.epoch_height >= h.epoch_height => false,
            _ => true,
        }
    }
```

**File:** chain/network/src/snapshot_hosts/mod.rs (L230-252)
```rust
    /// Updates the minimum epoch height to keep in the cache. This is called based on chain progression.
    /// Discards snapshot infos that are too old.
    fn update_discard_epoch_threshold(&mut self, epoch_height: EpochHeight) {
        let min_epoch_to_keep = epoch_height.saturating_sub(self.epoch_retention_window);
        if self.discard_snapshot_infos_below_epoch_height == Some(min_epoch_to_keep) {
            return;
        }

        self.discard_snapshot_infos_below_epoch_height = Some(min_epoch_to_keep);

        // Remove snapshot infos that are now below the retention window
        let mut new_hosts = LruCache::new(NonZeroUsize::new(self.hosts.cap().get()).unwrap());

        loop {
            let Some((peer_id, info)) = self.hosts.pop_lru() else { break };
            if info.epoch_height >= min_epoch_to_keep {
                new_hosts.push(peer_id, info);
            } else {
                self.remove_from_shard_hosts(&peer_id);
            }
        }
        self.hosts = new_hosts;
    }
```

**File:** chain/network/src/peer_manager/peer_manager_actor.rs (L893-935)
```rust
            NetworkRequests::StateRequestHeader { shard_id, sync_hash, sync_prev_prev_hash } => {
                // The node needs to include its own public address in the request
                // so that the response can be sent over a direct Tier3 connection.
                let Some(addr) = *self.state.my_public_addr.read() else {
                    return NetworkResponses::MyPublicAddrNotKnown;
                };

                // Select a peer which has advertised availability of the desired
                // state snapshot.
                let Some(peer_id) = self
                    .state
                    .snapshot_hosts
                    .select_host_for_header(&sync_prev_prev_hash, shard_id)
                else {
                    tracing::debug!(target: "network", %shard_id, ?sync_hash, "no snapshot hosts available");
                    return NetworkResponses::NoDestinationsAvailable;
                };

                let routed_message = self.state.sign_message(
                    &self.clock,
                    RawRoutedMessage {
                        target: PeerIdOrHash::PeerId(peer_id.clone()),
                        body: T2MessageBody::StateHeaderRequest(StateHeaderRequest {
                            shard_id,
                            sync_hash,
                            addr,
                        })
                        .into(),
                    },
                );

                self.state.pending_tier3_requests.insert(peer_id.clone(), self.clock.now());
                if !self.state.send_message_to_peer(
                    &self.clock,
                    tcp::Tier::T2,
                    routed_message,
                    &*self.transport,
                ) {
                    self.state.pending_tier3_requests.remove(&peer_id);
                    return NetworkResponses::RouteNotFound;
                }
                tracing::debug!(target: "network", %shard_id, ?sync_hash, %peer_id, "requesting state header from host");
                NetworkResponses::SelectedDestination(peer_id)
```

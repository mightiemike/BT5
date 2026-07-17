### Title
Unvalidated Shard-Layout Compatibility in `SnapshotHostInfo` Allows Any Peer to Poison State-Sync Host Selection — (File: `chain/network/src/network_protocol/state_sync.rs`)

### Summary

`SnapshotHostInfo::verify()` checks only signature validity and shard count. It never cross-validates the advertised `ShardId` values against the actual shard layout for the claimed epoch. Any unprivileged peer can self-sign a `SnapshotHostInfo` that claims to host arbitrary or non-existent shard IDs, passes all verification, propagates to every node in the network, and gets selected as a state-sync source — causing syncing nodes (including validators catching up) to route state-part requests to a peer that cannot serve them.

### Finding Description

`SnapshotHostInfo::verify()` enforces exactly two invariants:

```rust
// chain/network/src/network_protocol/state_sync.rs  lines 67-79
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

The signed payload is `hash_borsh((sync_hash, epoch_height, shards))`: [2](#0-1) 

The signature proves only that the peer signed those three fields. It does **not** prove:

1. `sync_hash` is a real block hash known to the chain.
2. `epoch_height` matches the actual epoch of `sync_hash`.
3. Each `ShardId` in `shards` exists in the shard layout for that epoch.

After passing `verify()`, the entry is inserted into `SnapshotHostsCache` and broadcast to all peers: [3](#0-2) 

The shard-specific routing table is then populated unconditionally from the advertised `shards` list: [4](#0-3) 

When a syncing node calls `select_host_for_part()` or `select_host_for_header()`, it draws from `hosts_for_shard`, which now contains the attacker's `PeerId` for every shard ID the attacker claimed: [5](#0-4) 

The peer manager then routes the actual `StateHeaderRequest` / `StatePartRequest` to the attacker: [6](#0-5) 

The `ShardId` type is a raw `u64` with no layout-membership check at construction time: [7](#0-6) 

After a resharding event, old `ShardId` values cease to exist in the new shard layout. A peer can advertise those stale or entirely fabricated IDs, pass verification, and be selected as a sync source for shards it cannot serve.

### Impact Explanation

**High — availability.** A syncing node (including a validator catching up after downtime) that has its `SnapshotHostsCache` poisoned with false shard advertisements will route state-part requests to the attacker. The attacker can silently drop requests or return garbage. Because the `PartPeerSelector` increments `num_requests` and only rotates to a new peer after exhausting the current batch, a single well-placed fake entry can stall state sync for an extended period. A validator unable to complete state sync cannot participate in consensus, directly threatening network liveness.

### Likelihood Explanation

**Medium.** Any peer with a valid key pair can execute this attack. No validator stake, admin key, or privileged role is required. The attacker only needs to observe the current `sync_hash` (broadcast openly in `SyncSnapshotHosts` messages) and self-sign a `SnapshotHostInfo` claiming all shard IDs up to `MAX_SHARDS_PER_SNAPSHOT_HOST_INFO`. The message propagates to all peers via the existing gossip mechanism.

### Recommendation

Extend `SnapshotHostInfo::verify()` (or `SnapshotHostsCache::insert()`) to cross-check each advertised `ShardId` against the shard layout for the epoch identified by `sync_hash`. Concretely:

1. Resolve the `EpochId` from `sync_hash` via the epoch manager.
2. Obtain the `ShardLayout` for that epoch.
3. Reject any `ShardId` in `self.shards` that is not present in `shard_layout.shard_ids()`.

Because `SnapshotHostsCache` currently has no access to the epoch manager, the cleanest fix is to add an optional epoch-manager reference to `SnapshotHostsCache::insert()` and perform the layout check there, returning a new `SnapshotHostInfoError::InvalidShardId` variant on mismatch.

### Proof of Concept

```
1. Attacker generates a fresh ED25519 key pair (node_key_attacker).
2. Attacker connects to the network and observes the current sync_hash
   broadcast in SyncSnapshotHosts messages.
3. Attacker constructs:
     peer_id    = PeerId::new(node_key_attacker.public_key())
     sync_hash  = <observed sync_hash>
     epoch_height = u64::MAX   // passes is_new() check (no prior entry)
     shards     = [0, 1, 2, ..., MAX_SHARDS_PER_SNAPSHOT_HOST_INFO-1]
                  // arbitrary ShardIds, including non-existent ones
     hash       = CryptoHash::hash_borsh((sync_hash, epoch_height, shards))
     signature  = node_key_attacker.sign(hash.as_ref())
4. Attacker sends PeerMessage::SyncSnapshotHosts { hosts: [crafted_info] }.
5. Every receiving node calls SnapshotHostsCache::insert():
     - shards.len() <= MAX_SHARDS_PER_SNAPSHOT_HOST_INFO  ✓
     - signature.verify(hash, peer_id.public_key())        ✓
   → entry inserted, broadcast to all peers.
6. Syncing nodes call select_host_for_part(sync_hash, shard_id, part_id).
   The attacker's PeerId is returned for every shard_id in the fake list.
7. StatePartRequest is routed to the attacker, who drops it.
   State sync stalls until timeout and retry logic exhausts all selectors.
``` [8](#0-7) [9](#0-8) [4](#0-3)

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

**File:** chain/network/src/snapshot_hosts/mod.rs (L183-190)
```rust
    fn add_to_shard_hosts(&mut self, info: &SnapshotHostInfo) {
        if self.current_state_sync_hash.as_ref() != Some(&info.sync_hash) {
            return;
        }
        for shard_id in &info.shards {
            self.hosts_for_shard.entry(*shard_id).or_default().insert(info.peer_id.clone());
        }
    }
```

**File:** chain/network/src/snapshot_hosts/mod.rs (L256-263)
```rust
    pub fn select_host_for_header(
        &mut self,
        sync_hash: &CryptoHash,
        shard_id: ShardId,
    ) -> Option<PeerId> {
        self.update_current_state_sync_hash(sync_hash);
        self.hosts_for_shard.get(&shard_id)?.iter().choose(&mut thread_rng()).cloned()
    }
```

**File:** chain/network/src/snapshot_hosts/mod.rs (L344-371)
```rust
    async fn verify(
        &self,
        data: Vec<Arc<SnapshotHostInfo>>,
    ) -> (Vec<Arc<SnapshotHostInfo>>, Option<SnapshotHostInfoError>) {
        // Filter out any data which is invalid, outdated or which we already have.
        if data.iter().map(|d| d.peer_id.clone()).collect::<HashSet<_>>().len() != data.len() {
            return (vec![], Some(SnapshotHostInfoError::DuplicatePeerId));
        }
        let new_data = {
            let inner = self.0.lock();
            data.into_iter().filter(|d| !d.shards.is_empty() && inner.is_new(d)).collect_vec()
        };
        // Verify the signatures in parallel.
        // Verification will stop at the first encountered error.
        let (data, verification_result) = concurrency::rayon::run(move || {
            concurrency::rayon::try_map_result(new_data.into_iter().par_bridge(), |d| {
                match d.verify() {
                    Ok(()) => Ok(d),
                    Err(err) => Err(err),
                }
            })
        })
        .await;
        match verification_result {
            Ok(()) => (data, None),
            Err(err) => (data, Some(SnapshotHostInfoError::VerificationError(err))),
        }
    }
```

**File:** chain/network/src/snapshot_hosts/mod.rs (L376-389)
```rust
    pub async fn insert(
        self: &Self,
        data: Vec<Arc<SnapshotHostInfo>>,
    ) -> (Vec<Arc<SnapshotHostInfo>>, Option<SnapshotHostInfoError>) {
        // Execute verification on the rayon threadpool.
        let (data, err) = self.verify(data).await;
        if data.is_empty() {
            return (vec![], err);
        }
        // Insert the successfully verified data.
        let mut inner = self.0.lock();
        data.iter().for_each(|d| inner.insert(d));
        (data, err)
    }
```

**File:** chain/network/src/peer_manager/peer_manager_actor.rs (L949-958)
```rust
                // Select a peer which has advertised availability of the desired
                // state snapshot.
                let Some(peer_id) = self.state.snapshot_hosts.select_host_for_part(
                    &sync_prev_prev_hash,
                    shard_id,
                    part_id,
                ) else {
                    tracing::debug!(target: "network", %shard_id, ?sync_hash, ?part_id, "no snapshot hosts available");
                    return NetworkResponses::NoDestinationsAvailable;
                };
```

**File:** core/primitives-core/src/types.rs (L80-105)
```rust
pub struct ShardId(u64);

impl ShardId {
    /// Create a new shard id. Please note that this function should not be used
    /// to convert a shard index (a number in 0..num_shards range) to ShardId.
    /// Instead the ShardId should be obtained from the shard_layout.
    ///
    /// ```rust, ignore
    /// // BAD USAGE:
    /// for shard_index in 0..num_shards {
    ///     let shard_id = ShardId::new(shard_index); // Incorrect!!!
    /// }
    /// ```
    /// ```rust, ignore
    /// // GOOD USAGE 1:
    /// for shard_index in 0..num_shards {
    ///     let shard_id = shard_layout.get_shard_id(shard_index);
    /// }
    /// // GOOD USAGE 2:
    /// for shard_id in shard_layout.shard_ids() {
    ///     let shard_id = shard_layout.get_shard_id(shard_index);
    /// }
    /// ```
    pub const fn new(id: u64) -> Self {
        Self(id)
    }
```

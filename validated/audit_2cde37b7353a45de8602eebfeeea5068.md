### Title
Stale `AnnounceAccount` Epoch Accepted Without Disk-Staleness Check, Poisoning Validator Routing Table - (File: `chain/network/src/announce_accounts/mod.rs`)

### Summary

`AnnounceAccountCache::add_accounts` uses only the in-memory `account_peers_broadcasted` LRU cache as the source of truth for epoch-staleness deduplication. After a node restart (or LRU eviction), that cache is empty. The staleness guard in `ViewClientActor` is skipped when `last_epoch` is `None`, so any peer can inject a validly-signed but epoch-stale `AnnounceAccount` that overwrites the on-disk current entry, poisoning the validator-to-peer routing table.

### Finding Description

`AnnounceAccount` carries an `epoch_id` that scopes its validity:

```rust
pub struct AnnounceAccount {
    pub account_id: AccountId,
    pub peer_id: PeerId,
    pub epoch_id: EpochId,   // "only valid for this epoch"
    pub signature: Signature,
}
``` [1](#0-0) 

The ingestion pipeline is:

1. `handle_sync_routing_table` fetches the "last known epoch" for each incoming account **only from `account_peers_broadcasted`**:

```rust
let old = network_state
    .account_announcements
    .get_broadcasted_announcements(rtu.accounts.iter().map(|a| &a.account_id));
``` [2](#0-1) 

2. `ViewClientActor` skips the epoch-ordering check entirely when `last_epoch` is `None`:

```rust
if let Some(last_epoch) = last_epoch {
    match self.epoch_manager.compare_epoch_id(&announce_account.epoch_id, &last_epoch) {
        Ok(Ordering::Greater) => {}
        _ => continue,
    }
}
``` [3](#0-2) 

3. `AnnounceAccountCache::add_accounts` only deduplicates by **exact epoch equality** against `account_peers_broadcasted`, never consulting `account_peers` (which is populated from disk):

```rust
if inner.account_peers_broadcasted.get(account_id).map(|x| &x.epoch_id)
    == Some(epoch_id)
{
    continue;
}
inner.account_peers.put(account_id.clone(), announcement.clone());
inner.account_peers_broadcasted.put(account_id.clone(), announcement.clone());
inner.store.set_account_announcement(account_id, &announcement);
``` [4](#0-3) 

`get_broadcasted_announcements` only reads `account_peers_broadcasted`, not `account_peers` (which is the cache that loads from disk via `get_announce`): [5](#0-4) 

After a node restart, `account_peers_broadcasted` is empty (it is an in-memory LRU cache, not persisted). The disk may hold a current-epoch announcement (e.g., epoch E2). An attacker who replays a validly-signed old announcement (epoch E1, E1 < E2) will:

- Receive `last_epoch = None` (nothing in `account_peers_broadcasted`)
- Bypass the `compare_epoch_id` guard in `ViewClientActor`
- Pass signature verification (the old announcement is legitimately signed by the validator)
- Overwrite the on-disk entry with the stale epoch E1 announcement

The same window opens whenever the LRU cache (size 10,000) evicts an entry while the disk still holds a newer one.

### Impact Explanation

The `AnnounceAccount` routing table is the sole mechanism by which non-validator nodes forward transactions to the correct chunk producer:

> "Each validator is regularly broadcasting `AnnounceAccount`, which is basically a pair of `(account_id, peer_id)`, to the whole network. This way each node knows which `peer_id` to send the message to." [6](#0-5) 

`send_message_to_account` falls back to `account_announcements.get_account_owner` for TIER2 routing: [7](#0-6) 

A poisoned entry routes transactions to a stale `peer_id` (the validator's old network identity). Those transactions are silently dropped. The validator's chunk production is starved of incoming transactions, degrading liveness.

### Likelihood Explanation

Old `AnnounceAccount` messages are broadcast to the entire network and are not secret. Any peer that was connected during a previous epoch has a copy. The attack requires:

1. Collecting a validly-signed old announcement (trivial — they are gossiped to all peers).
2. Connecting to a target node immediately after it restarts (or after LRU eviction of the entry).
3. Sending the stale announcement before the legitimate current one arrives.

Node restarts are routine (upgrades, crashes). The attack window is the interval between restart and receipt of the first legitimate `SyncRoutingTable` from an honest peer. An attacker who connects first wins the race.

### Recommendation

In `handle_sync_routing_table`, fetch the reference epoch from `account_peers` (which includes disk-loaded entries) rather than only from `account_peers_broadcasted`. Concretely, expose a `get_announcements_by_ids` method on `AnnounceAccountCache` that reads through to disk (via `get_announce`), and use that result to populate `last_epoch` before sending to `ViewClientActor`. This ensures the epoch-staleness guard in `ViewClientActor` is always armed with the best-known epoch, even after a restart.

Alternatively, persist `account_peers_broadcasted` to disk so it survives restarts, keeping the two caches in sync.

### Proof of Concept

```
1. Validator V broadcasts AnnounceAccount{account_id=V, peer_id=P2, epoch_id=E2, sig=σ2}
   → stored on disk of node N as the current entry.

2. Node N restarts.
   account_peers = {} (empty LRU)
   account_peers_broadcasted = {} (empty LRU)
   disk: {V → (P2, E2)}

3. Attacker A (who collected the old announcement from the network) connects to N
   and sends SyncRoutingTable containing:
   AnnounceAccount{account_id=V, peer_id=P1, epoch_id=E1, sig=σ1}   (E1 < E2, valid sig)

4. handle_sync_routing_table:
   old = get_broadcasted_announcements([V]) → {} (empty)
   accounts = [(AnnounceAccount{V,P1,E1,σ1}, None)]

5. ViewClientActor:
   last_epoch = None → epoch comparison SKIPPED
   check_signature_account_announce → Ok(true)  (σ1 is valid for (V,P1,E1))
   filtered = [AnnounceAccount{V,P1,E1,σ1}]

6. add_accounts([AnnounceAccount{V,P1,E1,σ1}]):
   account_peers_broadcasted.get(V) → None  (≠ Some(E1)) → not skipped
   account_peers[V] = (P1,E1)
   account_peers_broadcasted[V] = (P1,E1)
   disk[V] = (P1,E1)   ← stale entry overwrites current

7. Node N now routes all transactions for V to P1 (stale peer).
   Transactions are dropped; V's chunk production is starved.
``` [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** core/primitives/src/network.rs (L58-68)
```rust
#[derive(BorshSerialize, BorshDeserialize, PartialEq, Eq, Clone, Debug, Hash, ProtocolSchema)]
pub struct AnnounceAccount {
    /// AccountId to be announced.
    pub account_id: AccountId,
    /// PeerId from the owner of the account.
    pub peer_id: PeerId,
    /// This announcement is only valid for this `epoch`.
    pub epoch_id: EpochId,
    /// Signature using AccountId associated secret key.
    pub signature: Signature,
}
```

**File:** chain/network/src/peer/peer_actor.rs (L1404-1424)
```rust
        // For every announce we received, we fetch the last announce with the same account_id
        // that we already broadcasted. Client actor will both verify signatures of the received announces
        // as well as filter out those which are older than the fetched ones (to avoid overriding
        // a newer announce with an older one).
        let old = network_state
            .account_announcements
            .get_broadcasted_announcements(rtu.accounts.iter().map(|a| &a.account_id));
        let accounts: Vec<(AnnounceAccount, Option<EpochId>)> = rtu
            .accounts
            .into_iter()
            .map(|aa| {
                let id = aa.account_id.clone();
                (aa, old.get(&id).map(|old| old.epoch_id))
            })
            .collect();
        match network_state.client.send_async(AnnounceAccountRequest(accounts)).await {
            Ok(Err(ban_reason)) => conn.stop(Some(ban_reason)),
            Ok(Ok(accounts)) => network_state.add_accounts(accounts, tcp).await,
            Err(_) => {}
        }
    }
```

**File:** chain/client/src/view_client_actor.rs (L1762-1815)
```rust
impl Handler<AnnounceAccountRequest, Result<Vec<AnnounceAccount>, ReasonForBan>>
    for ViewClientActor
{
    fn handle(
        &mut self,
        msg: AnnounceAccountRequest,
    ) -> Result<Vec<AnnounceAccount>, ReasonForBan> {
        tracing::debug!(target: "client", ?msg);
        let _timer = metrics::VIEW_CLIENT_MESSAGE_TIME
            .with_label_values(&["AnnounceAccountRequest"])
            .start_timer();
        let AnnounceAccountRequest(announce_accounts) = msg;

        let mut filtered_announce_accounts = Vec::new();

        for (announce_account, last_epoch) in announce_accounts {
            // Keep the announcement if it is newer than the last announcement from
            // the same account.
            if let Some(last_epoch) = last_epoch {
                match self.epoch_manager.compare_epoch_id(&announce_account.epoch_id, &last_epoch) {
                    Ok(Ordering::Greater) => {}
                    _ => continue,
                }
            }

            match self.check_signature_account_announce(&announce_account) {
                Ok(true) => {
                    filtered_announce_accounts.push(announce_account);
                }
                // TODO(gprusak): Here we ban for broadcasting accounts which have been slashed
                // according to BlockInfo for the current chain tip. It is unfair,
                // given that peers do not have perfectly synchronized heads:
                // - AFAIU each block can introduce a slashed account, so the announcement
                //   could be OK at the moment that peer has sent it out.
                // - the current epoch_id is not related to announce_account.epoch_id,
                //   so it carry a perfectly valid (outdated) information.
                Ok(false) => {
                    return Err(ReasonForBan::InvalidSignature);
                }
                // Filter out this account. This covers both good reasons to ban the peer:
                // - signature didn't match the data and public_key.
                // - account is not a validator for the given epoch
                // and cases when we were just unable to validate the data (so we shouldn't
                // ban), for example when the node is not aware of the public key for the given
                // (account_id,epoch_id) pair.
                // We currently do NOT ban the peer for either.
                // TODO(gprusak): consider whether we should change that.
                Err(err) => {
                    tracing::debug!(target: "view_client", ?err, "failed to validate account announce signature");
                }
            }
        }
        Ok(filtered_announce_accounts)
    }
```

**File:** chain/network/src/announce_accounts/mod.rs (L63-88)
```rust
    pub(crate) fn add_accounts(
        &self,
        account_announcements: Vec<AnnounceAccount>,
    ) -> Vec<AnnounceAccount> {
        let mut inner = self.0.lock();
        let mut res = vec![];
        for announcement in account_announcements {
            let account_id = &announcement.account_id;
            let epoch_id = &announcement.epoch_id;

            // We skip broadcasting stuff that is already broadcasted.
            if inner.account_peers_broadcasted.get(account_id).map(|x| &x.epoch_id)
                == Some(epoch_id)
            {
                continue;
            }

            inner.account_peers.put(account_id.clone(), announcement.clone());
            inner.account_peers_broadcasted.put(account_id.clone(), announcement.clone());

            // Add account to store.
            inner.store.set_account_announcement(account_id, &announcement);
            res.push(announcement);
        }
        res
    }
```

**File:** chain/network/src/announce_accounts/mod.rs (L107-117)
```rust
    pub(crate) fn get_broadcasted_announcements<'a>(
        &'a self,
        account_ids: impl Iterator<Item = &'a AccountId>,
    ) -> HashMap<AccountId, AnnounceAccount> {
        let mut inner = self.0.lock();
        account_ids
            .filter_map(|id| {
                inner.account_peers_broadcasted.get(id).map(|a| (id.clone(), a.clone()))
            })
            .collect()
    }
```

**File:** docs/architecture/how/tx_routing.md (L57-63)
```markdown
Each validator is regularly (every `config.ttl_account_id_router`/2 seconds == 30
minutes in production) broadcasting so called `AnnounceAccount`, which is
basically a pair of `(account_id, peer_id)`, to the whole network. This way each
node knows which `peer_id` to send the message to.

Then it asks the routing table about the shortest path to the peer, and sends
the `ForwardTx` message to the peer.
```

**File:** chain/network/src/peer_manager/network_state/mod.rs (L841-844)
```rust
        } else if let Some(peer_id) = self.account_announcements.get_account_owner(account_id) {
            metrics::ACCOUNT_TO_PEER_LOOKUPS.with_label_values(&["AnnounceAccount"]).inc();
            peer_id
        } else {
```

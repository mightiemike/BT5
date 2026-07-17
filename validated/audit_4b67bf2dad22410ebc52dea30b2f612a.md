### Title
Unbounded `waiting_for_block` accumulation via unauthenticated deferred messages in `SpiceChunkValidatorActor` — (File: `chain/client/src/spice/chunk_validator_actor.rs`)

---

### Summary

`SpiceChunkValidatorActor` defers both `SpiceChunkStateWitness` and `SpiceChunkContractAccesses` messages into an unbounded `HashMap` (`waiting_for_block`) when the referenced block is not yet in the store — **before any signature, size, or distance-from-head validation is performed**. Any connected peer can inject an unlimited number of entries into this map by sending messages that reference non-existent block hashes, causing unbounded memory growth and denial-of-service of the Spice chunk-validation path. The non-Spice path (`handle_orphan_witness`) has all three guards; the Spice path has none, and the code itself carries explicit TODO comments acknowledging the gap.

---

### Finding Description

`SpiceChunkValidatorActor` owns the field:

```rust
waiting_for_block:
    HashMap<CryptoHash, (Vec<SpiceChunkStateWitness>, Vec<SpiceChunkContractAccesses>)>,
``` [1](#0-0) 

This is a plain `HashMap` with no capacity bound, no LRU eviction, and no TTL.

**Path 1 — `SpiceChunkStateWitness`:**

```rust
WitnessProcessingReadiness::NotReady => {
    // TODO(spice): Implement additional checks (size limit, distance to head)
    // before adding witness to `waiting_for_block`. See non-spice handle_orphan_witness().
    self.waiting_for_block.entry(chunk_id.block_hash).or_default().0.push(witness);
    Ok(())
}
``` [2](#0-1) 

No signature check, no size check, no distance-from-head check occurs before the witness is stored.

**Path 2 — `SpiceChunkContractAccesses`:**

```rust
Err(EpochError::EpochOutOfBounds(_)) | Err(EpochError::MissingBlock(_)) => {
    // TODO(spice): Implement additional checks (size limit, distance to head)
    // before adding accesses to `waiting_for_block`. See non-spice handle_orphan_witness().
    self.waiting_for_block.entry(chunk_id.block_hash).or_default().1.push(accesses);
    return Ok(());
}
``` [3](#0-2) 

The signature verification block that follows this branch is **never reached** when the block is absent:

```rust
let producers =
    self.epoch_manager.get_epoch_chunk_producers_for_shard(&epoch_id, chunk_id.shard_id)?;
let sender = producers.iter().find(|account_id| {
    ...
    accesses.verify_signature(validator.public_key())
});
``` [4](#0-3) 

The only pre-storage check for `SpiceChunkContractAccesses` is `MAX_CONTRACTS_PER_REQUEST` (1282 entries), which limits the payload of a single message but does not bound the number of distinct block-hash keys in the map. [5](#0-4) 

**Contrast with the non-Spice path**, which enforces all three guards before touching the orphan pool:

```rust
const ALLOWED_ORPHAN_WITNESS_DISTANCE_FROM_HEAD: Range<BlockHeight> = 2..6;
...
if !ALLOWED_ORPHAN_WITNESS_DISTANCE_FROM_HEAD.contains(&head_distance) { return ... }
if witness_size_u64 > self.max_orphan_witness_size { return ... }
// (signature already verified upstream before handle_orphan_witness is called)
self.orphan_witness_pool.lock().add_orphan_state_witness(witness, ...);
``` [6](#0-5) [7](#0-6) 

The `MAX_PENDING_CHUNKS = 24` constant bounds only `partial_chunk_data` (an `LruCache`), not `waiting_for_block`. [8](#0-7) 

---

### Impact Explanation

A connected peer (no validator stake required) can:

1. Craft `SpiceChunkContractAccesses` or `SpiceChunkStateWitness` messages referencing arbitrary, non-existent `block_hash` values.
2. Send them at high rate; each distinct `block_hash` creates a new `HashMap` entry.
3. `waiting_for_block` grows without bound, exhausting the validator's heap.
4. The validator OOMs or becomes unresponsive, halting Spice chunk endorsement for all shards it tracks.
5. Because `partial_chunk_data` (the LRU) is separate, legitimate witnesses that do arrive cannot be assembled — the actor thread is occupied or the process is dead.

The `SpiceChunkStateWitness` variant is worse per-entry (witnesses can be up to 64 MiB uncompressed per the in-file comment), while the `SpiceChunkContractAccesses` variant allows more distinct keys per unit of bandwidth.

---

### Likelihood Explanation

The Spice feature is gated by `protocol_feature_spice` (protocol version 180) and is not yet active on mainnet or testnet. However, the code is in production files and will be reachable once the feature activates. Any peer connected to a Spice-enabled validator node can send these messages; no stake, no special role, and no valid signature is required to trigger the deferred-storage path.

---

### Recommendation

Apply the same three guards the non-Spice orphan path uses, **before** inserting into `waiting_for_block`:

1. **Signature validation** — verify the witness/accesses signature against the expected chunk producer before deferring. If the epoch/block is unknown, drop rather than defer unsigned messages.
2. **Distance-from-head check** — reject messages whose `block_hash` height is outside `ALLOWED_ORPHAN_WITNESS_DISTANCE_FROM_HEAD` (or an equivalent Spice constant).
3. **Size cap** — reject witnesses exceeding `max_orphan_witness_size`.
4. **Capacity bound** — replace the plain `HashMap` with an `LruCache` (or equivalent bounded structure) so that even valid deferred messages cannot grow the map without limit.

The TODO comments at both deferral sites already point to `handle_orphan_witness` as the reference implementation.

---

### Proof of Concept

```
// Attacker sends N SpiceChunkContractAccesses messages, each with a distinct
// random block_hash that is not in the store, and contracts.len() <= 1282.
//
// For each message, handle_spice_contract_accesses reaches:
//   Err(EpochError::MissingBlock(_)) =>
//       self.waiting_for_block
//           .entry(chunk_id.block_hash)   // new key each time
//           .or_default()
//           .1
//           .push(accesses);              // stored without signature check
//
// After N messages:
//   waiting_for_block.len() == N   (unbounded HashMap)
//   memory consumed ≈ N × (sizeof(CryptoHash) + sizeof(SpiceChunkContractAccesses))
//
// The block referenced by each key never arrives, so entries are never drained.
// The validator node OOMs; Spice chunk endorsement halts.
``` [1](#0-0) [3](#0-2)

### Citations

**File:** chain/client/src/spice/chunk_validator_actor.rs (L50-50)
```rust
pub(crate) const MAX_PENDING_CHUNKS: usize = 24;
```

**File:** chain/client/src/spice/chunk_validator_actor.rs (L62-64)
```rust
    /// Data we cannot process yet because the referenced block is not in the store.
    waiting_for_block:
        HashMap<CryptoHash, (Vec<SpiceChunkStateWitness>, Vec<SpiceChunkContractAccesses>)>,
```

**File:** chain/client/src/spice/chunk_validator_actor.rs (L264-268)
```rust
            WitnessProcessingReadiness::NotReady => {
                // Block not ready: store for block arrival notification.
                // TODO(spice): Implement additional checks (size limit, distance to head) before adding witness to `waiting_for_block`. See non-spice handle_orphan_witness().
                self.waiting_for_block.entry(chunk_id.block_hash).or_default().0.push(witness);
                Ok(())
```

**File:** chain/client/src/spice/chunk_validator_actor.rs (L501-509)
```rust
        if accesses.contracts().len() > MAX_CONTRACTS_PER_REQUEST {
            tracing::debug!(
                target: "spice_chunk_validator",
                ?chunk_id,
                num_contracts = accesses.contracts().len(),
                "contract accesses message exceeds maximum number of contracts"
            );
            return Ok(());
        }
```

**File:** chain/client/src/spice/chunk_validator_actor.rs (L515-524)
```rust
            Err(EpochError::EpochOutOfBounds(_)) | Err(EpochError::MissingBlock(_)) => {
                // Block not in store yet — defer until it arrives.
                tracing::debug!(
                    target: "spice_chunk_validator",
                    ?chunk_id,
                    "contract accesses for block not yet available; deferring",
                );
                // TODO(spice): Implement additional checks (size limit, distance to head) before adding accesses to `waiting_for_block`. See non-spice handle_orphan_witness().
                self.waiting_for_block.entry(chunk_id.block_hash).or_default().1.push(accesses);
                return Ok(());
```

**File:** chain/client/src/spice/chunk_validator_actor.rs (L528-542)
```rust
        let producers =
            self.epoch_manager.get_epoch_chunk_producers_for_shard(&epoch_id, chunk_id.shard_id)?;
        // TODO(spice),TODO(spice-perf): We could get the expected public key from the message (or
        // by using sender if possible), check the signature, and then check the public id is in an expected hash set (or just iterate them), to avoid checking many signatures.
        let sender = producers.iter().find(|account_id| {
            let Ok(validator) =
                self.epoch_manager.get_validator_by_account_id(&epoch_id, account_id)
            else {
                return false;
            };
            accesses.verify_signature(validator.public_key())
        });
        let Some(sender) = sender.cloned() else {
            return Err(Error::Other("invalid spice contract accesses signature".to_owned()));
        };
```

**File:** chain/client/src/stateless_validation/chunk_validation_actor.rs (L46-46)
```rust
const ALLOWED_ORPHAN_WITNESS_DISTANCE_FROM_HEAD: Range<BlockHeight> = 2..6;
```

**File:** chain/client/src/stateless_validation/chunk_validation_actor.rs (L251-283)
```rust
        if !ALLOWED_ORPHAN_WITNESS_DISTANCE_FROM_HEAD.contains(&head_distance) {
            tracing::debug!(
                target: "chunk_validation",
                head_height = chain_head.height,
                "not saving an orphaned chunk state witness because its height isn't within the allowed height range"
            );
            return Ok(HandleOrphanWitnessOutcome::TooFarFromHead {
                witness_height,
                head_height: chain_head.height,
            });
        }

        // Don't save orphaned state witnesses which are bigger than the allowed limit.
        let witness_size_u64: u64 = witness_size as u64;
        if witness_size_u64 > self.max_orphan_witness_size {
            tracing::warn!(
                target: "chunk_validation",
                witness_height,
                ?witness_shard,
                witness_chunk = ?chunk_header.chunk_hash(),
                witness_prev_block = ?chunk_header.prev_block_hash(),
                witness_size = witness_size_u64,
                "not saving an orphaned chunk state witness because it's too big, this is unexpected"
            );
            return Ok(HandleOrphanWitnessOutcome::TooBig(witness_size_u64 as usize));
        }

        // Orphan witness is OK, save it to the pool
        tracing::debug!(target: "chunk_validation", "saving an orphaned chunk state witness to orphan pool");
        self.orphan_witness_pool
            .lock()
            .add_orphan_state_witness(witness, witness_size_u64 as usize);
        Ok(HandleOrphanWitnessOutcome::SavedToPool)
```

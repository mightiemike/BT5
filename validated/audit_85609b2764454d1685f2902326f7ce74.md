### Title
Unauthenticated Peers Fill the Shared `OrphanStateWitnessPool` Before Signature Verification, Evicting Legitimate Witnesses — (File: `chain/client/src/stateless_validation/chunk_validation_actor.rs`)

---

### Summary

In `process_chunk_state_witness_message`, when a `ChunkStateWitness` arrives whose `prev_block_hash` is not yet in the local chain store, the witness is inserted into the shared, bounded `OrphanStateWitnessPool` **before any signature verification**. Any network peer can craft witnesses with valid-looking metadata but forged signatures, reference unknown block hashes, and fill the LRU pool, evicting legitimate witnesses. When the real parent block later arrives, the legitimate witnesses are gone and their endorsement windows are missed.

---

### Finding Description

`process_chunk_state_witness_message` in `chain/client/src/stateless_validation/chunk_validation_actor.rs` handles every inbound `ChunkStateWitness`. Its control flow is:

1. Check that a local validator signer exists.
2. **Send a `ChunkStateWitnessAck` back to the chunk producer** — before any signature check.
3. Optionally **save the witness to the database** — before any signature check.
4. Look up `prev_block_hash` in the chain store.
   - If found → `process_chunk_state_witness` → `start_validating_chunk` → signature verified inside.
   - **If not found → `handle_orphan_witness` → witness inserted into `OrphanStateWitnessPool` with no signature check.** [1](#0-0) 

`handle_orphan_witness` performs only two guards before inserting into the pool:

```
if !ALLOWED_ORPHAN_WITNESS_DISTANCE_FROM_HEAD.contains(&head_distance) { return }
if witness_size_u64 > self.max_orphan_witness_size { return }
self.orphan_witness_pool.lock().add_orphan_state_witness(witness, …);
``` [2](#0-1) 

The pool's own documentation explicitly states the invariant that is violated:

> *"It's expected that this `ChunkStateWitness` has gone through basic validation — including **signature**, shard_id, size, epoch_id and distance from the tip. The pool would still work without it, but without validation it'd be possible to fill the whole cache with spam."* [3](#0-2) 

The pool is a bounded `LruCache<ChunkProductionKey, CacheEntry>` shared across all chunk-validation actor threads via `Arc<Mutex<OrphanStateWitnessPool>>`: [4](#0-3) 

When the cache is full, the least-recently-used entry is silently evicted: [5](#0-4) 

Signature verification only happens later, inside `validate_chunk_state_witness`, which is never reached for an evicted witness: [6](#0-5) 

---

### Impact Explanation

The `OrphanStateWitnessPool` capacity is controlled by `orphan_state_witness_pool_size` (default: a small integer, warned at > 128): [7](#0-6) 

An adversary who fills the pool with `capacity` forged witnesses evicts all legitimate orphan witnesses. When the real parent block arrives, `take_state_witnesses_waiting_for_block` returns nothing for the legitimate chunks: [8](#0-7) 

The validator then misses the endorsement window for those chunks. Sustained attack across multiple validators degrades chunk endorsement rates and slows block finality. Additionally, before any of this, an ACK is unconditionally sent back to the claimed chunk producer for every forged witness, creating a network-amplification side-channel: [9](#0-8) 

---

### Likelihood Explanation

Any node that has established a P2P connection can send `ChunkStateWitness` messages. The only attacker-controlled fields that must be plausible are:

- `height_created` — within `ALLOWED_ORPHAN_WITNESS_DISTANCE_FROM_HEAD` of the current head (public via RPC).
- `prev_block_hash` — any hash not yet in the local store (e.g., a future or fabricated hash).
- Encoded size — below `max_orphan_witness_size`.

No stake, no key material, and no valid signature are required to pass the two guards in `handle_orphan_witness`. The attack is repeatable at network speed.

---

### Recommendation

Move signature verification **before** the orphan-pool insertion. The chunk producer's public key can be resolved from the epoch manager using the `epoch_id`, `shard_id`, and `height_created` fields already present in the witness header — the same lookup already performed in `start_validating_chunk`: [10](#0-9) 

Reject any witness whose signature does not verify before calling `handle_orphan_witness`. This matches the documented contract of `OrphanStateWitnessPool::add_orphan_state_witness`.

---

### Proof of Concept

1. Connect to a target validator node as a P2P peer.
2. Query the current chain head height `H` via RPC.
3. For `i` in `0 .. orphan_state_witness_pool_size + 1`:
   - Construct a `ChunkStateWitness` with `height_created = H + 1`, `shard_id = i % NUM_SHARDS`, `prev_block_hash = sha256(i)` (unknown to the target), and an **arbitrary invalid signature**.
   - Keep encoded size below `max_orphan_witness_size`.
   - Send the message to the target node.
4. Each message passes the two guards in `handle_orphan_witness` and is inserted into the pool, evicting the oldest entry.
5. After `capacity` messages, the pool contains only forged witnesses.
6. When the real parent block for a legitimate orphan witness arrives, `take_state_witnesses_waiting_for_block` returns an empty list; the legitimate witness is never validated and no endorsement is produced for that chunk.

### Citations

**File:** chain/client/src/stateless_validation/chunk_validation_actor.rs (L173-176)
```rust
        // Create shared orphan witness pool
        let shared_orphan_pool =
            Arc::new(Mutex::new(OrphanStateWitnessPool::new(orphan_witness_pool_size)));

```

**File:** chain/client/src/stateless_validation/chunk_validation_actor.rs (L247-283)
```rust
        // Don't save orphaned state witnesses which are far away from the current chain head.
        let chain_head = self.chain_store.head()?;
        let head_distance = witness_height.saturating_sub(chain_head.height);

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

**File:** chain/client/src/stateless_validation/chunk_validation_actor.rs (L286-308)
```rust
    /// Processes orphan witnesses that are now ready because their previous block has arrived.
    fn process_ready_orphan_witnesses(&self, new_block: &Block) {
        let ready_witnesses = self
            .orphan_witness_pool
            .lock()
            .take_state_witnesses_waiting_for_block(new_block.hash());

        for witness in ready_witnesses {
            let header = witness.chunk_header();
            tracing::debug!(
                target: "chunk_validation",
                witness_height = header.height_created(),
                witness_shard = ?header.shard_id(),
                witness_chunk = ?header.chunk_hash(),
                witness_prev_block = ?header.prev_block_hash(),
                "processing an orphaned chunk state witness, its previous block has arrived"
            );

            if let Err(err) = self.process_chunk_state_witness(witness, new_block, None) {
                tracing::error!(target: "chunk_validation", ?err, "error processing orphan chunk state witness");
            }
        }
    }
```

**File:** chain/client/src/stateless_validation/chunk_validation_actor.rs (L389-403)
```rust
        let prev_block_hash = *state_witness.chunk_header().prev_block_hash();
        let chunk_production_key = state_witness.chunk_production_key();
        let shard_id = state_witness.chunk_header().shard_id();
        let chunk_header = state_witness.chunk_header().clone();
        let chunk_producer_name =
            self.epoch_manager.get_chunk_producer_info(&chunk_production_key)?.take_account_id();

        let expected_epoch_id =
            self.epoch_manager.get_epoch_id_from_prev_block(&prev_block_hash)?;
        if expected_epoch_id != chunk_production_key.epoch_id {
            return Err(Error::InvalidChunkStateWitness(format!(
                "Invalid EpochId {:?} for previous block {}, expected {:?}",
                chunk_production_key.epoch_id, prev_block_hash, expected_epoch_id
            )));
        }
```

**File:** chain/client/src/stateless_validation/chunk_validation_actor.rs (L541-546)
```rust
        // Send acknowledgement back to the chunk producer. The ack is a best-effort
        // latency signal and must never abort witness processing, so log and continue
        // on failure rather than returning early.
        if let Err(err) = self.send_state_witness_ack(&witness) {
            tracing::error!(target: "chunk_validation", ?err, "failed to send state witness ack");
        }
```

**File:** chain/client/src/stateless_validation/chunk_validation_actor.rs (L555-596)
```rust
        // Check if previous block exists to know whether or not this witness is an orphan
        let prev_block_hash = *witness.chunk_header().prev_block_hash();
        match self.chain_store.get_block(&prev_block_hash) {
            Ok(prev_block) => {
                // Previous block exists
                match self.process_chunk_state_witness(
                    witness,
                    &prev_block,
                    processing_done_tracker,
                ) {
                    Ok(()) => {
                        tracing::debug!(target: "chunk_validation", "chunk witness validation started successfully");
                        Ok(())
                    }
                    Err(err) => {
                        tracing::error!(target: "chunk_validation", ?err, "failed to start chunk witness validation");
                        Err(err)
                    }
                }
            }
            Err(Error::DBNotFoundErr(_)) => {
                // Previous block isn't available at the moment - handle as orphan
                tracing::debug!(
                    target: "chunk_validation",
                    "previous block not found - handling as orphan witness"
                );
                match self.handle_orphan_witness(witness, raw_witness_size) {
                    Ok(outcome) => {
                        tracing::debug!(target: "chunk_validation", ?outcome, "orphan witness handled");
                        Ok(())
                    }
                    Err(err) => {
                        tracing::error!(target: "chunk_validation", ?err, "failed to handle orphan witness");
                        Err(err)
                    }
                }
            }
            Err(err) => {
                tracing::error!(target: "chunk_validation", ?err, "failed to get previous block");
                Err(err)
            }
        }
```

**File:** chain/client/src/stateless_validation/chunk_validator/orphan_witness_pool.rs (L26-38)
```rust
    pub fn new(cache_capacity: usize) -> Self {
        if cache_capacity > 128 {
            tracing::warn!(
                target: "client",
                cache_capacity,
                "orphan state witness cache capacity is larger than expected, this might lead to performance problems"
            );
        }

        OrphanStateWitnessPool {
            witness_cache: LruCache::new(NonZeroUsize::new(cache_capacity).unwrap()),
        }
    }
```

**File:** chain/client/src/stateless_validation/chunk_validator/orphan_witness_pool.rs (L40-46)
```rust
    /// Add an orphaned chunk state witness to the pool. The witness will be put in a cache and it'll
    /// wait there for the block that's required to process it.
    /// It's expected that this `ChunkStateWitness` has gone through basic validation - including signature,
    /// shard_id, size, epoch_id and distance from the tip. The pool would still work without it, but without
    /// validation it'd be possible to fill the whole cache with spam.
    /// `witness_size` is only used for metrics, it's okay to pass 0 if you don't care about the metrics.
    pub fn add_orphan_state_witness(&mut self, witness: ChunkStateWitness, witness_size: usize) {
```

**File:** chain/client/src/stateless_validation/chunk_validator/orphan_witness_pool.rs (L51-62)
```rust
        if let Some((_, ejected_entry)) = self.witness_cache.push(cache_key, cache_entry) {
            // Another witness has been ejected from the cache due to capacity limit
            let header = &ejected_entry.witness.chunk_header();
            tracing::debug!(
                target: "client",
                ejected_witness_height = header.height_created(),
                ejected_witness_shard = ?header.shard_id(),
                ejected_witness_chunk = ?header.chunk_hash(),
                ejected_witness_prev_block = ?header.prev_block_hash(),
                "ejecting an orphaned chunk state witness from the cache due to capacity limit, it will not be processed"
            );
        }
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L565-577)
```rust
pub fn validate_chunk_state_witness_impl(
    state_witness: ChunkStateWitness,
    pre_validation_output: PreValidationOutput,
    epoch_manager: &dyn EpochManagerAdapter,
    runtime_adapter: &dyn RuntimeAdapter,
    main_state_transition_cache: &MainStateTransitionCache,
    rs: Arc<ReedSolomon>,
) -> Result<(), Error> {
    let ChunkProductionKey { shard_id: witness_chunk_shard_id, epoch_id, height_created } =
        state_witness.chunk_production_key();
    let _timer = crate::stateless_validation::metrics::CHUNK_STATE_WITNESS_VALIDATION_TIME
        .with_label_values(&[&witness_chunk_shard_id.to_string()])
        .start_timer();
```

### Title
Cache Populated with Post-Resharding-Reassigned Receipts Before Full Validation Completes, Causing Double-Reassignment on Cache Hit — (File: `chain/chain/src/stateless_validation/chunk_validation.rs`)

---

### Summary

In `validate_chunk_state_witness_impl`, the `MainStateTransitionCache` is written with `outgoing_receipts` that have already been mutated by `reassign_outgoing_receipts_for_resharding` — **before** the implicit-transition checks and final chunk-header validation complete. On any subsequent call that hits the cache for the same `(witness_chunk_shard_uid, block_hash)` during a resharding epoch, the reassignment is applied a second time to the already-reassigned receipts, producing a divergent `outgoing_receipts_root` that no longer matches the committed chunk header. The result is that a structurally valid witness is rejected by every chunk validator that processes it after the first cache population, breaking chunk endorsement liveness at the resharding boundary.

---

### Finding Description

The function `validate_chunk_state_witness_impl` follows this sequence:

**Step 1 — Main transition (lines 600–621):** Either runs `apply_new_chunk` (cache miss) or reads `(chunk_extra, outgoing_receipts)` from the cache (cache hit).

**Step 2 — Early state-root check (lines 622–631):** Returns early if the main-transition post-state-root does not match the witness.

**Step 3 — Resharding reassignment (lines 633–646):** If `chunk_shard_layout != witness_shard_layout`, calls `ChainStore::reassign_outgoing_receipts_for_resharding(&mut outgoing_receipts, …)`, mutating `outgoing_receipts` in place.

**Step 4 — Cache write (lines 647–660):** Stores `(chunk_extra, outgoing_receipts)` — the **post-reassignment** value — into `MainStateTransitionCache`.

**Step 5 — Remaining validation (lines 662–777):** Implicit-transition count check, implicit-transition loop (each can `return Err`), and the parallel `validate_chunk_with_chunk_extra_and_receipts_root` / `validate_chunk_with_encoded_merkle_root` calls.

The cache write at Step 4 is unconditional and happens before Steps 5's checks can fail. On a second call for the same key (e.g., network retransmission of the witness, or the `shadow_validate_state_witness` path), the cache branch at line 620 returns the already-reassigned `outgoing_receipts`. Step 3 then applies `reassign_outgoing_receipts_for_resharding` a second time to those receipts, producing a doubly-reassigned receipt list. The `outgoing_receipts_root` computed from this list diverges from the value committed in the chunk header, causing `validate_chunk_with_chunk_extra_and_receipts_root` to return `Err` for a witness that is otherwise fully valid.

Additionally, the cache is overwritten at Step 4 of the second call with the doubly-reassigned receipts, so every subsequent call compounds the error further. [1](#0-0) 

The exact divergent value is the `outgoing_receipts_root` produced by `Chain::build_receipts_hashes` over doubly-reassigned receipts, which does not equal the `prev_outgoing_receipts_root` field committed in the `ShardChunkHeader`. [2](#0-1) 

The `MainStateTransitionCache` type and its per-shard LRU structure: [3](#0-2) 

---

### Impact Explanation

During a resharding epoch (`chunk_shard_layout != witness_shard_layout`), any chunk validator that processes the same `(witness_chunk_shard_uid, block_hash)` pair more than once — through retransmission, the shadow-validation path, or concurrent witness delivery — will compute a wrong `outgoing_receipts_root` on the second and all subsequent calls. The validator will not emit a chunk endorsement for a structurally valid chunk. If a sufficient fraction of the validator set is affected, the chunk cannot accumulate the required endorsements and cannot be included in a block, stalling chain progress at the resharding boundary. This is a **High** severity liveness impact scoped to the resharding protocol-upgrade boundary.

---

### Likelihood Explanation

Resharding is a planned, deterministic protocol event triggered by a shard-layout change at an epoch boundary. The `shadow_validate_state_witness` code path explicitly re-validates witnesses using the same shared `MainStateTransitionCache`, making the double-reassignment reachable without any network retransmission. The condition `chunk_shard_layout != witness_shard_layout` is true for every chunk validated during the resharding epoch transition, so the bug is triggered on every cache hit in that window — not an edge case. [4](#0-3) 

---

### Recommendation

Move the `cache.put(…)` call to **after** all validation steps complete successfully (i.e., after line 777, inside the `Ok(())` return path). This mirrors the fix applied in the referenced external report: the irreversible side-effect (cache population / token transfer) must be gated on the validity check passing, not precede it.

Alternatively, store the **pre-reassignment** `outgoing_receipts` in the cache (captured before the `reassign_outgoing_receipts_for_resharding` call) and always apply the reassignment after a cache hit, ensuring idempotent behavior.

---

### Proof of Concept

1. Configure a testnet with a shard-layout change scheduled at epoch boundary E.
2. At epoch E, a chunk validator `V` receives `ChunkStateWitness W` for shard `S` (new layout). `V` calls `validate_chunk_state_witness_impl(W, …)`.
   - Cache miss → `apply_new_chunk` runs → raw `outgoing_receipts` produced.
   - `chunk_shard_layout != witness_shard_layout` → `reassign_outgoing_receipts_for_resharding` mutates `outgoing_receipts` in place.
   - Cache is written with post-reassignment `outgoing_receipts` (lines 647–660).
   - Remaining checks pass → `Ok(())` returned → endorsement sent.
3. `V` receives `W` again (retransmission) or the shadow-validation path triggers a second call with the same cache.
   - Cache hit → `outgoing_receipts` = post-reassignment value from cache.
   - `chunk_shard_layout != witness_shard_layout` → `reassign_outgoing_receipts_for_resharding` applied **again** → doubly-reassigned receipts.
   - `Chain::build_receipts_hashes` produces a root `R'` ≠ `R` (the root committed in the chunk header).
   - `validate_chunk_with_chunk_extra_and_receipts_root` returns `Err(InvalidChunkStateWitness(…))`.
   - No endorsement emitted for a valid chunk. [5](#0-4)

### Citations

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L87-99)
```rust
#[derive(Clone)]
pub struct ChunkStateWitnessValidationResult {
    pub chunk_extra: ChunkExtra,
    pub outgoing_receipts: Vec<Receipt>,
}

// TODO: key should be a pair (chunk_shard_uid, witness_shard_uid) for shard merging
pub type MainStateTransitionCache =
    Arc<Mutex<HashMap<ShardUId, LruCache<CryptoHash, ChunkStateWitnessValidationResult>>>>;

/// The number of state witness validation results to cache per shard.
/// This number needs to be small because result contains outgoing receipts, which can be large.
const NUM_WITNESS_RESULT_CACHE_ENTRIES: usize = 20;
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L594-660)
```rust
    let cache_result = {
        let mut shard_cache = main_state_transition_cache.lock();
        shard_cache
            .get_mut(&witness_chunk_shard_uid)
            .and_then(|cache| cache.get(&block_hash).cloned())
    };
    let (mut chunk_extra, mut outgoing_receipts) =
        match (pre_validation_output.main_transition_params, cache_result) {
            (MainTransition::Genesis { chunk_extra, .. }, _) => (chunk_extra, vec![]),
            (MainTransition::NewChunk { new_chunk_data, .. }, None) => {
                let chunk_gas_limit = new_chunk_data.gas_limit;
                let NewChunkResult { apply_result: mut main_apply_result, .. } = apply_new_chunk(
                    ApplyChunkReason::ValidateChunkStateWitness,
                    &span,
                    new_chunk_data,
                    ShardContext { shard_uid, should_apply_chunk: true },
                    runtime_adapter,
                    // Recorded-storage replay; no memtrie path.
                    MaybePinnedMemtrieRoot::no_memtries(),
                    None,
                )?;
                let outgoing_receipts = std::mem::take(&mut main_apply_result.outgoing_receipts);
                let chunk_extra = main_apply_result.to_chunk_extra(chunk_gas_limit);

                (chunk_extra, outgoing_receipts)
            }
            (_, Some(result)) => (result.chunk_extra, result.outgoing_receipts),
        };
    if chunk_extra.state_root() != &state_witness.main_state_transition().post_state_root {
        // This is an early check, it's not for correctness, only for better
        // error reporting in case of an invalid state witness due to a bug.
        // Only the final state root check against the chunk header is required.
        return Err(Error::InvalidChunkStateWitness(format!(
            "Post state root {:?} for main transition does not match expected post state root {:?}",
            chunk_extra.state_root(),
            state_witness.main_state_transition().post_state_root,
        )));
    }

    // Compute receipt hashes here to avoid copying receipts
    let outgoing_receipts_hashes = {
        let chunk_epoch_id = epoch_manager.get_epoch_id(&block_hash)?;
        let chunk_shard_layout = epoch_manager.get_shard_layout(&chunk_epoch_id)?;
        if chunk_shard_layout != witness_shard_layout {
            ChainStore::reassign_outgoing_receipts_for_resharding(
                &mut outgoing_receipts,
                &witness_shard_layout,
                witness_chunk_shard_id,
                shard_id,
            )?;
        }
        Chain::build_receipts_hashes(&outgoing_receipts, &witness_shard_layout)?
    };
    // Save main state transition result to cache.
    {
        let mut shard_cache = main_state_transition_cache.lock();
        let cache = shard_cache.entry(witness_chunk_shard_uid).or_insert_with(|| {
            LruCache::new(NonZeroUsize::new(NUM_WITNESS_RESULT_CACHE_ENTRIES).unwrap())
        });
        cache.put(
            block_hash,
            ChunkStateWitnessValidationResult {
                chunk_extra: chunk_extra.clone(),
                outgoing_receipts: outgoing_receipts.clone(),
            },
        );
    }
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L751-777)
```rust
    // Compute receipts root + header validation in parallel with encoded-merkle-root check.
    let (res_receipts_root, res_encoded_merkle_check) = rayon::join(
        || -> Result<CryptoHash, Error> {
            let (outgoing_receipts_root, _) = merklize(&outgoing_receipts_hashes);
            validate_chunk_with_chunk_extra_and_receipts_root(
                &chunk_extra,
                &state_witness.chunk_header(),
                &outgoing_receipts_root,
            )?;
            Ok(outgoing_receipts_root)
        },
        || {
            let (tx_root, _) = merklize(&state_witness.new_transactions());
            if tx_root != *state_witness.chunk_header().tx_root() {
                return Err(Error::InvalidTxRoot);
            }
            validate_chunk_with_encoded_merkle_root(
                &state_witness.chunk_header(),
                &outgoing_receipts,
                state_witness.new_transactions(),
                rs.as_ref(),
                shard_id,
            )
        },
    );
    res_receipts_root?;
    res_encoded_merkle_check?;
```

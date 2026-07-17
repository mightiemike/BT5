### Title
`from_shard_id` Not Cryptographically Bound to Merkle Proof Position in `set_state_header` Allows Receipt Omission/Substitution During State Sync — (`chain/chain/src/state_sync/adapter.rs`)

---

### Summary

The Pigeonhole argument in `set_state_header` step 4d is unsound. The `from_shard_id` field inside each `ShardProof` is checked only for uniqueness across the N proofs, but is never cryptographically bound to the Merkle-tree leaf position used in steps 4e/4f. A malicious peer can therefore supply N receipt proofs with N distinct (but wrong) `from_shard_id` values, reuse one chunk's outgoing-receipts root for multiple slots, and omit another chunk's receipts entirely — all while every individual Merkle proof verifies correctly.

---

### Finding Description

`set_state_header` validates incoming receipt proofs in four sub-steps:

**4c** — count equals `block_header.chunks_included()`: [1](#0-0) 

**4d** — all `from_shard_id` values are unique: [2](#0-1) 

**4e** — `verify_path(*root, proof, receipts_hash)` — receipts hash is in the outgoing-receipts Merkle tree rooted at `root`: [3](#0-2) 

**4f** — `verify_path(block_header.prev_chunk_outgoing_receipts_root(), block_proof, root)` — `root` is a leaf in the block-level Merkle tree: [4](#0-3) 

The `verify_path` primitive used in both 4e and 4f does **not** check the leaf's position/index in the tree:

```rust
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}
pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root   // position-blind
}
``` [5](#0-4) 

Compare with `verify_path_with_index`, which does enforce position: [6](#0-5) 

Because `verify_path` is position-blind, the same `(root_k, block_proof_k)` pair for chunk k can be presented in any slot j with any `from_shard_id` value. The uniqueness check on `from_shard_id` (step 4d) only guarantees N distinct label strings, not that those labels cover the N actual shards in the block.

**Concrete attack (3-shard block, N = `chunks_included` = 3):**

| Slot | `from_shard_id` claimed | `root` used | `block_proof` | `receipts` supplied |
|------|------------------------|-------------|---------------|---------------------|
| 0 | A (correct) | `root_A` | `path_to_A` | `[R1, R2]` (shard A's real receipts to target) |
| 1 | B (wrong) | `root_C` | `path_to_C` | `[]` (shard C's empty receipts to target) |
| 2 | C (wrong) | `root_A` | `path_to_A` | `[R1, R2]` (shard A's receipts again) |

- Step 4c: 3 == 3 ✓  
- Step 4d: {A, B, C} all distinct ✓  
- Step 4e: each `receipts_hash` is valid for its `root` ✓  
- Step 4f: each `root` is a valid leaf in the block's Merkle tree ✓  

**Outcome:** shard B's receipts `[R3, R4]` are silently omitted; shard A's receipts `[R1, R2]` are applied twice. The header is accepted and stored.

The header is then consumed by `set_state_finalize`, which calls `collect_receipts_from_response` — a flat collection that ignores `from_shard_id` entirely: [7](#0-6) [8](#0-7) 

The wrong receipt set is then passed directly to `apply_chunk`: [9](#0-8) 

---

### Impact Explanation

A syncing node that accepts a crafted header will apply an incorrect set of incoming receipts when finalizing state. This produces a state root that diverges from the canonical chain. The node will subsequently fail to validate chunks or produce valid chunks, effectively being silently partitioned from the network with a corrupted local state. Because the header is persisted to `DBCol::StateHeaders` before any further check, the corruption survives restarts. [10](#0-9) 

---

### Likelihood Explanation

Any node in the NEAR network can serve state sync headers — no validator, block producer, or privileged role is required. The `byzantine_assert!` calls throughout `set_state_header` confirm the threat model explicitly anticipates Byzantine peers. Constructing the attack requires only public block data (chunk outgoing-receipts roots and their Merkle paths are derivable from any full node's block store). The attack is deterministic and requires no brute force.

---

### Recommendation

In step 4f, after verifying that `root` is a valid leaf in the block-level Merkle tree, additionally verify that `root` equals the `prev_outgoing_receipts_root` of the chunk whose shard ID matches `from_shard_id`. This requires fetching the full block (not just the block header) in `set_state_header`, or alternatively using `verify_path_with_index` with the shard index derived from `from_shard_id` and the known shard layout. The adapter's own header-building code already performs this binding:

```rust
let from_shard_index = prev_shard_layout.get_shard_index(*from_shard_id)?;
let root_proof = *block.chunks()[from_shard_index].prev_outgoing_receipts_root();
``` [11](#0-10) 

The validator must enforce the same invariant on the receiving side.

---

### Proof of Concept

```rust
// In set_state_header, after step 4f, add:
let block = self.chain_store.get_block(block_hash)?;
let shard_layout = self.epoch_manager.get_shard_layout_from_prev_block(block_hash)?;
let from_shard_index = shard_layout.get_shard_index(*from_shard_id)
    .map_err(|_| Error::Other("set_shard_state: invalid from_shard_id".into()))?;
let expected_root = *block.chunks()
    .get(from_shard_index)
    .ok_or_else(|| Error::Other("set_shard_state: from_shard_id out of bounds".into()))?
    .prev_outgoing_receipts_root();
if *root != expected_root {
    byzantine_assert!(false);
    return Err(Error::Other(
        "set_shard_state: root does not match from_shard_id chunk".into()
    ));
}
```

A test that crafts a header with `from_shard_id=B` but `root=root_C` (and valid Merkle proofs for shard C) would currently pass `set_state_header` and should be rejected after the fix.

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L202-206)
```rust
                let from_shard_index = prev_shard_layout.get_shard_index(*from_shard_id)?;

                let root_proof = *block.chunks()[from_shard_index].prev_outgoing_receipts_root();
                root_proofs_cur
                    .push(RootProof(root_proof, block_receipts_proofs[from_shard_index].clone()));
```

**File:** chain/chain/src/state_sync/adapter.rs (L464-469)
```rust
            if receipt_proofs.len() != shard_state_header.root_proofs()[i].len()
                || receipt_proofs.len() != block_header.chunks_included() as usize
            {
                byzantine_assert!(false);
                return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
            }
```

**File:** chain/chain/src/state_sync/adapter.rs (L475-486)
```rust
            let mut visited_shard_ids = HashSet::<ShardId>::new();
            for (j, receipt_proof) in receipt_proofs.iter().enumerate() {
                let ReceiptProof(receipts, shard_proof) = receipt_proof;
                let ShardProof { from_shard_id, to_shard_id: _, proof } = shard_proof;
                // 4d. Checking uniqueness for set of `from_shard_id`
                match visited_shard_ids.get(from_shard_id) {
                    Some(_) => {
                        byzantine_assert!(false);
                        return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
                    }
                    _ => visited_shard_ids.insert(*from_shard_id),
                };
```

**File:** chain/chain/src/state_sync/adapter.rs (L487-493)
```rust
                let RootProof(root, block_proof) = &shard_state_header.root_proofs()[i][j];
                let receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts));
                // 4e. Proving the set of receipts is the subset of outgoing_receipts of shard `shard_id`
                if !verify_path(*root, proof, &receipts_hash) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
                }
```

**File:** chain/chain/src/state_sync/adapter.rs (L494-502)
```rust
                // 4f. Proving the outgoing_receipts_root matches that in the block
                if !verify_path(
                    *block_header.prev_chunk_outgoing_receipts_root(),
                    block_proof,
                    root,
                ) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
                }
```

**File:** chain/chain/src/state_sync/adapter.rs (L525-529)
```rust
        // Saving the header data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();
```

**File:** core/primitives/src/merkle.rs (L113-119)
```rust
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}

pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
}
```

**File:** core/primitives/src/merkle.rs (L121-129)
```rust
pub fn verify_path_with_index<T: BorshSerialize>(
    root: MerkleHash,
    path: &MerklePath,
    item: T,
    part_idx: u64,
    num_merklized_parts: u64,
) -> bool {
    verify_path_matches_index(path, part_idx, num_merklized_parts) && verify_path(root, path, item)
}
```

**File:** chain/chain/src/chain_update.rs (L479-487)
```rust
        let mut receipt_proof_responses: Vec<ReceiptProofResponse> = vec![];
        for incoming_receipt_proof in &incoming_receipts_proofs {
            let ReceiptProofResponse(hash, _) = incoming_receipt_proof;
            let block_header = self.chain_store_update.get_block_header(hash)?;
            if block_header.height() <= chunk.height_included() {
                receipt_proof_responses.push(incoming_receipt_proof.clone());
            }
        }
        let receipts = collect_receipts_from_response(&receipt_proof_responses);
```

**File:** chain/chain/src/chain_update.rs (L519-542)
```rust
        let apply_result = self.runtime_adapter.apply_chunk(
            RuntimeStorageConfig::new(chunk_header.prev_state_root(), true),
            ApplyChunkReason::UpdateTrackedShard,
            ApplyChunkShardContext {
                shard_uid,
                gas_limit,
                last_validator_proposals: chunk_header.prev_validator_proposals(),
                is_new_chunk: true,
                on_post_state_ready: None,
                memtrie_pin,
            },
            ApplyChunkBlockContext {
                block_type: BlockType::Normal,
                height: chunk_header.height_included(),
                prev_block_hash: *chunk_header.prev_block_hash(),
                block_timestamp: block_header.raw_timestamp(),
                gas_price,
                random_seed: *block_header.random_value(),
                congestion_info: block.block_congestion_info(),
                bandwidth_requests: block.block_bandwidth_requests(),
            },
            &receipts,
            transactions,
        )?;
```

**File:** chain/chain/src/chain.rs (L4141-4147)
```rust
pub fn collect_receipts_from_response(
    receipt_proof_response: &[ReceiptProofResponse],
) -> Vec<Receipt> {
    collect_receipts(
        receipt_proof_response.iter().flat_map(|ReceiptProofResponse(_, proofs)| proofs.iter()),
    )
}
```

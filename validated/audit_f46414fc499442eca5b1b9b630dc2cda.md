### Title
Cross-Component Signature Grafting in `ChunkEndorsementV2::verify()` Allows Cache Poisoning — (`core/primitives/src/stateless_validation/chunk_endorsement.rs`)

---

### Summary

`ChunkEndorsementV2::verify()` validates `inner` (containing `chunk_hash`) and `metadata` (containing `shard_id`, `epoch_id`, `height_created`) via two **independent** signature checks with no cross-binding between them. An unprivileged network observer can combine the `inner`+`signature` from a legitimate endorsement for chunk A with the `metadata`+`metadata_signature` from a legitimate endorsement for chunk B (both signed by the same validator key), produce a `ChunkEndorsementV2` that passes `verify()`, and inject it into the block producer's endorsement cache under `key_B` with `chunk_hash_A`. This poisons the cache slot for that validator+key_B, causing the legitimate endorsement for chunk B to be silently dropped by the dedup guard, and the poisoned entry to be filtered out by `collect_chunk_endorsements`.

---

### Finding Description

**Structural flaw — no cross-binding between `inner` and `metadata`:**

`ChunkEndorsementInnerV1` contains only `chunk_hash` and a `signature_differentiator`: [1](#0-0) 

`ChunkEndorsementMetadata` contains only `account_id`, `shard_id`, `epoch_id`, `height_created`: [2](#0-1) 

`ChunkEndorsementV2::verify()` checks the two signatures independently — `signature` over `inner`, `metadata_signature` over `metadata` — with no field from one appearing in the other: [3](#0-2) 

Because neither signed blob references the other, a mixed struct where `inner` comes from endorsement A and `metadata` comes from endorsement B (same signer key) satisfies both checks simultaneously. `verify()` returns `true`.

**Validation path that accepts the mixed endorsement:**

`validate_chunk_endorsement` calls `validate_chunk_relevant_as_validator` (using `metadata_B`'s key, which is valid) and then `validate_chunk_endorsement_signature` which calls `endorsement.verify(validator.public_key())`: [4](#0-3) 

Both checks pass for the mixed endorsement.

**Cache poisoning via dedup guard:**

`process_chunk_endorsement` stores the result under `key_B` (from `metadata_B`) with `chunk_hash_A` (from `inner_A`): [5](#0-4) 

The dedup guard at the top of `process_chunk_endorsement` returns early if the validator's account_id is already present for `key_B`: [6](#0-5) 

If the mixed endorsement arrives first, the legitimate endorsement for chunk B from that validator is silently dropped.

**Poisoned entry filtered out at collection time:**

`collect_chunk_endorsements` filters cached entries by `chunk_hash == chunk_header.chunk_hash()`: [7](#0-6) 

Since `chunk_hash_A != chunk_hash_B`, the poisoned entry is excluded. The validator's endorsement for chunk B is never counted.

---

### Impact Explanation

An attacker who can observe endorsement messages on the network (endorsements are broadcast to block producers) and send crafted `ChunkEndorsement::V2` messages to a block producer node can selectively suppress individual validators' endorsements for targeted chunks. If applied to enough validators assigned to a shard, this can prevent a chunk from accumulating sufficient endorsement stake, stalling block production for that shard. The attack requires no validator, block producer, or node-admin privileges — only the ability to observe network traffic and send P2P messages.

---

### Likelihood Explanation

Endorsements are broadcast over the gossip network. Any network participant can observe them. The attacker needs two endorsements from the same validator key for two different `ChunkProductionKey` values (different height, shard, or epoch). Since validators endorse chunks at every height for their assigned shards, such pairs are routinely available. The attack window is a race between the crafted message and the legitimate endorsement, but the attacker can target the block producer directly and send the crafted message first.

---

### Recommendation

Cross-bind `inner` and `metadata` at signing time so that neither can be grafted onto the other. The simplest fix is to include the `ChunkProductionKey` fields (`shard_id`, `epoch_id`, `height_created`) inside `ChunkEndorsementInnerV1` alongside `chunk_hash`, so that a single signature covers both the chunk identity and the production key. Alternatively, sign a combined blob of `borsh(inner) || borsh(metadata)` as a single atomic unit. Either approach ensures that a signature valid for chunk A's inner cannot be paired with chunk B's metadata.

---

### Proof of Concept

```rust
// Pseudocode — both endorsements produced by the same validator key
let end_a = ChunkEndorsement::new(epoch_id, &header_a, &signer); // chunk A
let end_b = ChunkEndorsement::new(epoch_id, &header_b, &signer); // chunk B, same signer

// Extract components (fields are pub(crate) but accessible within the crate or via test helpers)
let ChunkEndorsement::V2(v2_a) = end_a;
let ChunkEndorsement::V2(v2_b) = end_b;

// Graft: inner+signature from A, metadata+metadata_signature from B
let mixed = ChunkEndorsement::V2(ChunkEndorsementV2 {
    inner: v2_a.inner,           // chunk_hash_A
    signature: v2_a.signature,   // sig over inner_A — valid for signer's key
    metadata: v2_b.metadata,     // shard_id_B, epoch_id_B, height_B
    metadata_signature: v2_b.metadata_signature, // sig over metadata_B — valid for signer's key
});

// verify() returns true — both independent checks pass
assert!(mixed.verify(&signer.public_key()));

// mixed.chunk_production_key() == key_B, mixed.chunk_hash() == chunk_hash_A
// Injecting `mixed` before the real end_b poisons the cache slot for (key_B, signer_account)
// collect_chunk_endorsements for chunk B then filters it out (chunk_hash_A != chunk_hash_B)
// and the real end_b is dropped by the dedup guard
```

### Citations

**File:** core/primitives/src/stateless_validation/chunk_endorsement.rs (L111-118)
```rust
impl ChunkEndorsementV2 {
    fn verify(&self, public_key: &PublicKey) -> bool {
        let inner = borsh::to_vec(&self.inner).unwrap();
        let metadata = borsh::to_vec(&self.metadata).unwrap();
        self.signature.verify(&inner, public_key)
            && self.metadata_signature.verify(&metadata, public_key)
    }
}
```

**File:** core/primitives/src/stateless_validation/chunk_endorsement.rs (L120-126)
```rust
#[derive(Debug, Clone, PartialEq, Eq, BorshSerialize, BorshDeserialize, ProtocolSchema)]
pub struct ChunkEndorsementMetadata {
    account_id: AccountId,
    shard_id: ShardId,
    epoch_id: EpochId,
    height_created: BlockHeight,
}
```

**File:** core/primitives/src/stateless_validation/chunk_endorsement.rs (L130-138)
```rust
struct ChunkEndorsementInnerV1 {
    chunk_hash: ChunkHash,
    signature_differentiator: SignatureDifferentiator,
}

impl ChunkEndorsementInnerV1 {
    fn new(chunk_hash: ChunkHash) -> Self {
        Self { chunk_hash, signature_differentiator: "ChunkEndorsement".to_owned() }
    }
```

**File:** chain/client/src/stateless_validation/validate.rs (L492-504)
```rust
fn validate_chunk_endorsement_signature(
    epoch_manager: &dyn EpochManagerAdapter,
    endorsement: &ChunkEndorsement,
) -> Result<(), Error> {
    let validator = epoch_manager.get_validator_by_account_id(
        &endorsement.chunk_production_key().epoch_id,
        &endorsement.account_id(),
    )?;
    if !endorsement.verify(validator.public_key()) {
        return Err(Error::InvalidChunkEndorsement);
    }
    Ok(())
}
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L48-54)
```rust
        {
            let cache = self.chunk_endorsements.lock();
            if cache.peek(&key).is_some_and(|entry| entry.contains_key(account_id)) {
                tracing::debug!(target: "client", ?endorsement, "already received chunk endorsement");
                return Ok(());
            }
        }
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L59-63)
```rust
                let mut cache = self.chunk_endorsements.lock();
                cache.get_or_insert_mut(key, || HashMap::new()).insert(
                    account_id.clone(),
                    (endorsement.chunk_hash(), endorsement.signature()),
                );
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L107-111)
```rust
        let validator_signatures = entry
            .into_iter()
            .filter(|(_, (chunk_hash, _))| chunk_hash == chunk_header.chunk_hash())
            .map(|(account_id, (_, signature))| (account_id, signature.clone()))
            .collect();
```

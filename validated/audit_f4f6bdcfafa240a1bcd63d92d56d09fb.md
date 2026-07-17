Let me look at the exact code paths more carefully.

Now I have all the information needed. Let me verify the critical equality between `DEFAULT_CRYPTO_HASH` and `Trie::EMPTY_ROOT`.

The code evidence is conclusive. Here is the analysis:

---

### Title
State sync `set_state_header` accepts attacker-supplied `StateRootNode::empty()` for any Spice-era (V6) chunk due to `DEFAULT_CRYPTO_HASH == Trie::EMPTY_ROOT` identity collision — (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`ShardChunkHeaderInner::V6` (Spice tx-only) has no `prev_state_root` field. Its `prev_state_root()` accessor silently returns `DEFAULT_CRYPTO_HASH = &CryptoHash::new()` (all-zero bytes). `Trie::EMPTY_ROOT` is defined as `StateRoot::new()` — also all-zero bytes. These two constants are identical. As a result, `set_state_header`'s call to `validate_state_root_node` with a V6 chunk's "state root" hits the `state_root == &Trie::EMPTY_ROOT` fast-path, which accepts `StateRootNode::empty()` as unconditionally valid. A malicious peer can serve a `ShardStateSyncResponseHeaderV2` carrying a real, chain-verified V6 chunk but a fabricated `state_root_node = StateRootNode::empty()`, and the receiving node will store it without error.

### Finding Description

**Step 1 — V6 returns DEFAULT_CRYPTO_HASH for `prev_state_root`:** [1](#0-0) [2](#0-1) 

`ShardChunkHeaderInnerV6SpiceTxOnly` has no `prev_state_root` field. The accessor returns `DEFAULT_CRYPTO_HASH = &CryptoHash::new()` (32 zero bytes) in release builds — the `debug_assert!` is a no-op in production. [3](#0-2) 

**Step 2 — `Trie::EMPTY_ROOT` is the same all-zero value:** [4](#0-3) 

`StateRoot` is a type alias for `CryptoHash`. `StateRoot::new()` and `CryptoHash::new()` both produce 32 zero bytes. Therefore `DEFAULT_CRYPTO_HASH == Trie::EMPTY_ROOT` is always `true`.

**Step 3 — `set_state_header` passes this value directly to `validate_state_root_node`:** [5](#0-4) 

**Step 4 — `validate_state_root_node` takes the EMPTY_ROOT fast-path and accepts `StateRootNode::empty()`:** [6](#0-5) 

Because `state_root == &Trie::EMPTY_ROOT` is `true`, the function returns `Valid` if and only if `state_root_node == &StateRootNode::empty()`. An attacker who supplies exactly `StateRootNode::empty()` passes this check for any V6 chunk, regardless of the real state root.

**Step 5 — `set_state_part` is also affected:** [7](#0-6) 

`set_state_part` re-reads `prev_state_root()` from the stored chunk header, again obtaining `DEFAULT_CRYPTO_HASH = Trie::EMPTY_ROOT`, so `validate_state_part` is called against the empty-trie root. An attacker-supplied empty state part passes this check, and the node applies an empty trie as its post-sync state.

### Impact Explanation

A syncing node that accepts a fabricated `ShardStateSyncResponseHeaderV2` for a Spice-era shard will:
1. Store the header with `state_root_node = StateRootNode::empty()`.
2. Compute `num_parts = get_num_state_parts(0)` → 1 part.
3. Accept an empty state part from the attacker.
4. Apply an empty trie as the shard's state, diverging from the canonical chain state.

The node will subsequently produce or validate blocks against an incorrect state root, causing it to be slashed or permanently fork from the network.

### Likelihood Explanation

The path is reachable by any peer that can respond to state sync requests — no validator or operator privilege is required. The only precondition is that the Spice protocol (V6 chunks) is active on the chain. The `debug_assert!` guard is compiled out in release builds, so the silent `DEFAULT_CRYPTO_HASH` return is the production behavior.

### Recommendation

`set_state_header` must explicitly reject V6 chunk headers before calling `validate_state_root_node`, or `ShardChunkHeaderInner::prev_state_root()` must return `Err`/`Option::None` for V6 so callers cannot silently receive a sentinel that collides with `Trie::EMPTY_ROOT`. The `DEFAULT_CRYPTO_HASH` sentinel must not be a value that passes any security-critical equality check.

### Proof of Concept

```
1. Obtain a real Spice-era block B containing a V6 chunk C for shard S.
2. Construct ShardStateSyncResponseHeaderV2 {
       chunk: C,                          // real, passes validate_chunk_proofs + Merkle proof
       chunk_proof: <valid Merkle proof>,
       prev_chunk_header: ...,
       prev_chunk_proof: ...,
       incoming_receipts_proofs: ...,
       root_proofs: ...,
       state_root_node: StateRootNode::empty(),  // fabricated
   }
3. Deliver this response to a syncing node as the answer to its state sync request.
4. set_state_header:
     chunk_inner = C.take_header().take_inner()  // ShardChunkHeaderInner::V6
     chunk_inner.prev_state_root()               // returns DEFAULT_CRYPTO_HASH = [0u8;32]
     validate_state_root_node(StateRootNode::empty(), [0u8;32])
       → state_root == &Trie::EMPTY_ROOT  ✓
       → state_root_node == &StateRootNode::empty()  ✓
       → Valid
5. Header is stored. Node requests 1 state part.
6. Attacker serves an empty state part; set_state_part accepts it.
7. Node applies empty trie as shard S state.
```

### Citations

**File:** core/primitives/src/sharding/shard_chunk_header_inner.rs (L11-13)
```rust
// When removing CryptoHash fields from new versions to be safe we return defaults instead of
// panicking.
const DEFAULT_CRYPTO_HASH: &CryptoHash = &CryptoHash::new();
```

**File:** core/primitives/src/sharding/shard_chunk_header_inner.rs (L29-40)
```rust
    pub fn prev_state_root(&self) -> &StateRoot {
        match self {
            Self::V1(inner) => &inner.prev_state_root,
            Self::V2(inner) => &inner.prev_state_root,
            Self::V3(inner) => &inner.prev_state_root,
            Self::V4(inner) => &inner.prev_state_root,
            Self::V5(inner) => &inner.prev_state_root,
            Self::V6(_) => {
                debug_assert!(false, "Transaction only header doesn't include prev_state_root");
                DEFAULT_CRYPTO_HASH
            }
        }
```

**File:** core/primitives/src/sharding/shard_chunk_header_inner.rs (L429-448)
```rust
// V5 -> V6: a version for spice of a chunk header including only transactions (no previous
// execution results).
#[derive(BorshSerialize, BorshDeserialize, Clone, PartialEq, Eq, Debug, ProtocolSchema)]
pub struct ShardChunkHeaderInnerV6SpiceTxOnly {
    /// Previous block hash.
    pub prev_block_hash: CryptoHash,
    pub encoded_merkle_root: CryptoHash,
    pub encoded_length: u64,
    pub height_created: BlockHeight,
    /// Shard index.
    pub shard_id: ShardId,
    // TODO(spice): remove prev_outgoing_receipts_root. We have it for now
    // so that some of the existing validations pass. List of outgoing receipts is always empty,
    // but it wouldn't mean that prev_outgoing_receipts_root is CryptoHash::default() since it's
    // computed as root of merkle tree of those empty lists from all shards.
    /// Previous chunk's outgoing receipts merkle root.
    pub prev_outgoing_receipts_root: CryptoHash,
    /// Tx merkle root.
    pub tx_root: CryptoHash,
}
```

**File:** core/store/src/trie/mod.rs (L604-605)
```rust
impl Trie {
    pub const EMPTY_ROOT: StateRoot = StateRoot::new();
```

**File:** chain/chain/src/state_sync/adapter.rs (L512-523)
```rust
        // 5. Checking that state_root_node is valid
        let chunk_inner = chunk.take_header().take_inner();
        if matches!(
            self.runtime_adapter.validate_state_root_node(
                shard_state_header.state_root_node(),
                chunk_inner.prev_state_root(),
            ),
            StateRootNodeValidationResult::Invalid
        ) {
            byzantine_assert!(false);
            return Err(Error::Other("set_shard_state failed: state_root_node is invalid".into()));
        }
```

**File:** chain/chain/src/state_sync/adapter.rs (L542-553)
```rust
        let chunk = shard_state_header.take_chunk();
        let state_root = *chunk.take_header().take_inner().prev_state_root();
        if matches!(
            self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part),
            StatePartValidationResult::Invalid
        ) {
            byzantine_assert!(false);
            return Err(Error::Other(format!(
                "set_state_part failed: validate_state_part failed. state_root={:?}",
                state_root
            )));
        }
```

**File:** chain/chain/src/runtime/mod.rs (L1551-1557)
```rust
        if state_root == &Trie::EMPTY_ROOT {
            return if state_root_node == &StateRootNode::empty() {
                StateRootNodeValidationResult::Valid
            } else {
                StateRootNodeValidationResult::Invalid
            };
        }
```

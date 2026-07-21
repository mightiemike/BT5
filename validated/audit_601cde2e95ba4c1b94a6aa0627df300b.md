Now I have a complete picture of the code. Let me trace the full attack path precisely.

**Key facts established:**

1. **Production path**: The P2P sync client uses `TryFrom<protobuf::StateDiffsResponse> for DataOrFin<StateDiffChunk>` → `TryFrom<protobuf::ContractDiff> for ContractDiff` (lines 216-241), **not** the `ThinStateDiff` path. Both converters have the identical deduplication pattern.

2. **Deduplication in `ContractDiff` converter** (lines 233-237):
```rust
let storage_diffs = value
    .values
    .into_iter()
    .map(|stored_value| stored_value.try_into())
    .collect::<Result<IndexMap<StorageKey, Felt>, _>>()?;
```
`IndexMap::collect` silently applies last-write-wins for duplicate keys. V1 is dropped, V2 survives.

3. **`StateDiffChunk::len()` counts post-deduplication entries** (sync.rs lines 149-157):
```rust
let mut result = contract_diff.storage_diffs.len(); // deduplicated count
```

4. **`parse_data_for_block` length check** (client/state_diff.rs lines 95-99):
```rust
current_state_diff_len += state_diff_chunk.len(); // uses deduplicated count
```
The `target_state_diff_len` comes from the block header's `state_diff_length`, which equals `ThinStateDiff::len()` — counting unique entries only.

5. **`unite_state_diffs` duplicate check** (lines 150-153) only catches duplicates **across separate `ContractDiff` messages**, not within a single message — because deduplication already happened before `unite_state_diffs` is called.

6. **No `state_diff_commitment` verification** in `parse_data_for_block`. The only guard is `state_diff_length`.

**Concrete attack:**
- Correct block has 1 storage entry: `(K → V1)` → `state_diff_length = 1`
- Attacker sends `ContractDiff.values = [(K, V1), (K, V2)]`
- After `collect::<IndexMap<...>>()`: `storage_diffs = {K: V2}` (V1 silently dropped)
- `state_diff_chunk.len()` = 1 → length check passes (1 == 1)
- `unite_state_diffs` sees no conflict (only 1 key in the map)
- Wrong value V2 is committed to storage

---

### Title
Silent Last-Write-Wins Deduplication of Duplicate Storage Keys in P2P `ContractDiff` Deserialization Allows Malicious Peer to Corrupt Synced State — (`crates/apollo_protobuf/src/converters/state_diff.rs`)

### Summary
`TryFrom<protobuf::ContractDiff> for ContractDiff` (the production P2P sync path) collects the `values` repeated field into an `IndexMap` without checking for duplicate keys. A malicious peer can send a single `ContractDiff` message with two entries for the same `StorageKey` (K→V1, K→V2). The `IndexMap` silently drops V1 and retains V2. Because `StateDiffChunk::len()` counts post-deduplication entries, the `state_diff_length` guard in `parse_data_for_block` passes. No `state_diff_commitment` hash verification exists in the P2P sync client path. The wrong value V2 is written to the syncing node's persistent state.

### Finding Description

The production deserialization path is:

`StateDiffsResponse` bytes → `TryFrom<protobuf::StateDiffsResponse> for DataOrFin<StateDiffChunk>` → `TryFrom<protobuf::ContractDiff> for ContractDiff` [1](#0-0) 

The `collect::<Result<IndexMap<StorageKey, Felt>, _>>()` call is the deduplication site. `IndexMap::from_iter` (and `.collect()`) applies last-write-wins for duplicate keys — this is standard Rust `HashMap`/`IndexMap` behavior and produces no error.

The length guard in `parse_data_for_block` compares `current_state_diff_len` (accumulated via `state_diff_chunk.len()`) against `target_state_diff_len` from the block header: [2](#0-1) 

`StateDiffChunk::len()` counts `contract_diff.storage_diffs.len()` — the post-deduplication size: [3](#0-2) 

`ThinStateDiff::len()` (which equals `state_diff_length` in the block header) also counts unique storage entries: [4](#0-3) 

So if the correct block has N unique storage entries, the attacker sends N+1 protobuf entries (with one duplicate key), the deduplication produces N unique entries, `state_diff_chunk.len()` = N, and the length check passes exactly.

The cross-chunk duplicate check in `unite_state_diffs` only fires when the same key appears in two **separate** `ContractDiff` messages: [5](#0-4) 

It cannot detect intra-message duplicates because they were already collapsed by `collect()` before `unite_state_diffs` is called.

There is no `state_diff_commitment` verification anywhere in `parse_data_for_block`. The only post-assembly check is `validate_deprecated_declared_classes_non_conflicting`: [6](#0-5) 

The same deduplication bug also exists in the legacy `TryFrom<protobuf::ContractDiff> for ThinStateDiff` path (marked TODO-remove): [7](#0-6) 

### Impact Explanation

A malicious P2P peer causes the syncing node to commit a wrong storage value for a specific `(ContractAddress, StorageKey)` pair. This wrong value is then:
- Persisted to the node's storage via `append_state_diff` [8](#0-7) 
- Used as the authoritative state for all subsequent RPC queries, fee estimations, and simulations served by that node
- Propagated to the Merkle trie committer via `ThinStateDiff → StateDiff` conversion [9](#0-8) 

Impact: **Critical** — wrong storage value written to state; contracts read a different value than what the block producer committed.

### Likelihood Explanation

Any node that connects to a malicious P2P peer during sync is vulnerable. No special privileges are required — any participant in the P2P network can serve crafted `StateDiffsResponse` messages. The attack is deterministic and requires only crafting a protobuf message with a repeated `values` field containing two entries for the same key.

### Recommendation

In `TryFrom<protobuf::ContractDiff> for ContractDiff` (and the `ThinStateDiff` variant), after collecting into the `IndexMap`, verify that the number of entries in the map equals the number of entries in `value.values`. If they differ, a duplicate key was present — return a `ProtobufConversionError`. Alternatively, iterate and use `insert`-returning-`Some` to detect the first duplicate and error immediately.

### Proof of Concept

```rust
// In crates/apollo_protobuf/src/converters/state_diff.rs (test module)
#[test]
fn duplicate_storage_key_last_write_wins() {
    use crate::protobuf::{self, Felt252};
    use crate::sync::ContractDiff;

    let key_bytes = vec![0u8; 32]; // key K
    let v1_bytes  = vec![1u8; 32]; // value V1
    let v2_bytes  = vec![2u8; 32]; // value V2

    let proto = protobuf::ContractDiff {
        address: Some(protobuf::Address { elements: vec![0u8; 32] }),
        values: vec![
            protobuf::ContractStoredValue {
                key:   Some(Felt252 { elements: key_bytes.clone() }),
                value: Some(Felt252 { elements: v1_bytes.clone() }),
            },
            protobuf::ContractStoredValue {
                key:   Some(Felt252 { elements: key_bytes.clone() }),
                value: Some(Felt252 { elements: v2_bytes.clone() }),
            },
        ],
        ..Default::default()
    };

    let diff: ContractDiff = proto.try_into().unwrap();
    // Only 1 entry survives (last-write-wins); V1 is silently dropped.
    assert_eq!(diff.storage_diffs.len(), 1);
    // The surviving value is V2, not V1.
    let surviving = diff.storage_diffs.values().next().unwrap();
    assert_eq!(surviving.to_bytes_be(), v2_bytes.as_slice());
    // V1 is lost — this is the bug.
}
```

### Citations

**File:** crates/apollo_protobuf/src/converters/state_diff.rs (L110-115)
```rust
            let storage_values = value
                .values
                .into_iter()
                .map(|stored_value| stored_value.try_into())
                .collect::<Result<IndexMap<StorageKey, Felt>, _>>()?;
            IndexMap::from_iter([(contract_address, storage_values)])
```

**File:** crates/apollo_protobuf/src/converters/state_diff.rs (L233-237)
```rust
        let storage_diffs = value
            .values
            .into_iter()
            .map(|stored_value| stored_value.try_into())
            .collect::<Result<IndexMap<StorageKey, Felt>, _>>()?;
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L33-35)
```rust
        async move {
            storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
            STATE_SYNC_STATE_MARKER.set_lossy(self.1.unchecked_next().0);
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-96)
```rust
            let target_state_diff_len = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_number)?
                .expect("A header with number lower than the header marker is missing")
                .state_diff_length
                .ok_or(P2pSyncClientError::OldHeaderInStorage {
                    block_number,
                    missing_field: "state_diff_length",
                })?;

            while current_state_diff_len < target_state_diff_len {
                let maybe_state_diff_chunk = state_diff_chunks_response_manager
                    .next()
                    .await
                    .ok_or(ParseDataError::BadPeer(BadPeerError::SessionEndedWithoutFin {
                        type_description: Self::TYPE_DESCRIPTION,
                    }))?;
                let Some(state_diff_chunk) = maybe_state_diff_chunk?.0 else {
                    if current_state_diff_len == 0 {
                        return Ok(None);
                    } else {
                        return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength {
                            expected_length: target_state_diff_len,
                            possible_lengths: vec![current_state_diff_len],
                        }));
                    }
                };
                prev_result_len = current_state_diff_len;
                if state_diff_chunk.is_empty() {
                    return Err(ParseDataError::BadPeer(BadPeerError::EmptyStateDiffPart));
                }
                // It's cheaper to calculate the length of `state_diff_part` than the length of
                // `result`.
                current_state_diff_len += state_diff_chunk.len();
                unite_state_diffs(&mut result, state_diff_chunk)?;
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L106-107)
```rust
            validate_deprecated_declared_classes_non_conflicting(&result)?;
            Ok(Some((result, block_number)))
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L147-162)
```rust
            if !contract_diff.storage_diffs.is_empty() {
                match state_diff.storage_diffs.get_mut(&contract_diff.contract_address) {
                    Some(storage_diffs) => {
                        for (k, v) in contract_diff.storage_diffs {
                            if storage_diffs.insert(k, v).is_some() {
                                return Err(BadPeerError::ConflictingStateDiffParts);
                            }
                        }
                    }
                    None => {
                        state_diff
                            .storage_diffs
                            .insert(contract_diff.contract_address, contract_diff.storage_diffs);
                    }
                }
            }
```

**File:** crates/apollo_protobuf/src/sync.rs (L147-162)
```rust
    pub fn len(&self) -> usize {
        match self {
            StateDiffChunk::ContractDiff(contract_diff) => {
                let mut result = contract_diff.storage_diffs.len();
                if contract_diff.class_hash.is_some() {
                    result += 1;
                }
                if contract_diff.nonce.is_some() {
                    result += 1;
                }
                result
            }
            StateDiffChunk::DeclaredClass(_) => 1,
            StateDiffChunk::DeprecatedDeclaredClass(_) => 1,
        }
    }
```

**File:** crates/starknet_api/src/state.rs (L110-122)
```rust
    /// This has the same value as `state_diff_length` in the corresponding `BlockHeader`.
    pub fn len(&self) -> usize {
        let mut result = 0usize;
        result += self.deployed_contracts.len();
        result += self.class_hash_to_compiled_class_hash.len();
        result += self.deprecated_declared_classes.len();
        result += self.nonces.len();

        for (_contract_address, storage_diffs) in &self.storage_diffs {
            result += storage_diffs.len();
        }
        result
    }
```

**File:** crates/starknet_committer/src/block_committer/input.rs (L115-147)
```rust
impl From<ThinStateDiff> for StateDiff {
    fn from(
        ThinStateDiff {
            class_hash_to_compiled_class_hash,
            deployed_contracts,
            storage_diffs,
            nonces,
            ..
        }: ThinStateDiff,
    ) -> Self {
        Self {
            address_to_class_hash: deployed_contracts.into_iter().collect(),
            address_to_nonce: nonces.into_iter().collect(),
            class_hash_to_compiled_class_hash: class_hash_to_compiled_class_hash
                .into_iter()
                .map(|(k, v)| (k, CompiledClassHash(v.0)))
                .collect(),
            storage_updates: storage_diffs
                .into_iter()
                .map(|(address, updates)| {
                    (
                        address,
                        updates
                            .into_iter()
                            .map(|(key, value)| {
                                (StarknetStorageKey(key), StarknetStorageValue(value))
                            })
                            .collect(),
                    )
                })
                .collect(),
        }
    }
```

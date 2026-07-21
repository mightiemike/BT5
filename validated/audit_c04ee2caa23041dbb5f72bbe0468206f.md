Based on my full trace of the code, this is a valid finding. Here is the complete analysis:

---

### Title
Peer-Supplied `block_hash` Accepted Without Recomputation or Signature Verification in P2P Sync, Stored and Served by RPC as Authoritative — (`crates/apollo_protobuf/src/converters/header.rs`)

### Summary

The `TryFrom<protobuf::SignedBlockHeader> for SignedBlockHeader` converter blindly copies the wire `block_hash` field into `BlockHeader.block_hash` with no recomputation against the other header fields and no call to `verify_block_signature`. The p2p sync client then writes this header directly to storage, and the RPC layer serves the stored `block_hash` as the authoritative hash for that block number.

### Finding Description

**Step 1 – Converter (no recomputation).** [1](#0-0) 

The converter extracts `block_hash` from the protobuf wire value and wraps it in `BlockHash`. There is no call to `calculate_block_hash` and no comparison against the Poseidon hash of the accompanying header fields (`block_number`, `state_root`, `sequencer`, `timestamp`, `gas_prices`, `starknet_version`, commitments, `parent_hash`).

**Step 2 – P2P sync client validation (no hash or signature check).** [2](#0-1) 

`parse_data_for_block` performs exactly two checks: block number ordering and signatures-vector length. There is no call to `verify_block_signature` and no call to `calculate_block_hash`. A grep across the entire `crates/apollo_p2p_sync` tree confirms zero uses of `verify_block_signature` or `sequencer_pub_key`.

**Step 3 – Storage write (peer value stored verbatim).** [3](#0-2) 

`write_to_storage` calls `append_header` with the peer-supplied `block_header` (including the peer-controlled `block_hash`) and commits. The storage layer also inserts the peer-supplied hash into the `block_hash_to_number` reverse-lookup table: [4](#0-3) 

**Step 4 – RPC serves the stored value as authoritative.**

`starknet_getBlockWithTxHashes` reads the stored `BlockHeader.block_hash` directly from storage and returns it to callers. The `block_hash` field is part of `BLOCK_HEADER` in the OpenRPC schema and is returned verbatim.

**Contrast with central sync.** The central sync path (`apollo_central_sync`) does call `verify_block_signature` and `verify_parent_block_hash` before storing. The p2p sync path has neither guard. [5](#0-4) 

**What `verify_block_signature` signs.** The sequencer signs `poseidon_hash(block_hash, state_diff_commitment)`. Because the p2p sync never calls this function, a peer can supply any `block_hash` value alongside a valid-looking signature (or any signature, since the length check is the only gate), and the node will accept and store it. [6](#0-5) 

### Impact Explanation

A malicious peer responding to a `BlockHeadersRequest` can set `block_hash` to an arbitrary `Felt` (e.g., `0xdeadbeef`). The syncing node will:
1. Store that value as `BlockHeader.block_hash` for the given block number.
2. Index it in the `block_hash_to_number` table, corrupting hash-based lookups.
3. Return it from `starknet_getBlockWithTxHashes`, `starknet_getBlockWithTxs`, `starknet_blockHashAndNumber`, and `starknet_syncing`.
4. Cause downstream clients that use the returned hash as `parent_hash` for the next block to build an incorrect chain.

This matches the allowed impact: **High — RPC returns an authoritative-looking wrong value.**

### Likelihood Explanation

Any peer the node connects to during p2p sync can trigger this. No special privilege is required; the attacker only needs to be a reachable peer that the node queries for block headers. The node has no fallback recomputation or multi-peer cross-check for `block_hash`.

### Recommendation

1. After deserializing a `SignedBlockHeader` from a peer, recompute the block hash using `calculate_block_hash` and reject the header (mark peer as bad) if it does not match the wire value.
2. Call `verify_block_signature` (using the known sequencer public key) before storing any peer-supplied header, mirroring the guard already present in `apollo_central_sync`.
3. Add a `BadPeerError` variant for `BlockHashMismatch` and `InvalidBlockSignature` in `block_data_stream_builder.rs`.

### Proof of Concept

```rust
// In a test that controls the mock peer response:
let fake_hash = BlockHash(Felt::from(0xdeadbeef_u64));
mock_header_responses_manager
    .send_response(DataOrFin(Some(SignedBlockHeader {
        block_header: BlockHeader {
            block_hash: fake_hash,          // attacker-controlled
            block_header_without_hash: BlockHeaderWithoutHash {
                block_number: BlockNumber(0),
                ..Default::default()
            },
            state_diff_length: Some(0),
            ..Default::default()
        },
        signatures: vec![BlockSignature::default()], // length check passes
    })))
    .await
    .unwrap();

// After sync advances:
let txn = storage_reader.begin_ro_txn().unwrap();
let stored = txn.get_block_header(BlockNumber(0)).unwrap().unwrap();
assert_eq!(stored.block_hash, fake_hash); // passes — 0xdeadbeef stored verbatim
```

The existing test `signed_headers_basic_flow` already demonstrates this behavior (it sends arbitrary random hashes and asserts they are stored unchanged), confirming the absence of any recomputation guard. [7](#0-6)

### Citations

**File:** crates/apollo_protobuf/src/converters/header.rs (L59-63)
```rust
        let block_hash = value
            .block_hash
            .ok_or(missing("SignedBlockHeader::block_hash"))?
            .try_into()
            .map(BlockHash)?;
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L28-50)
```rust
    fn write_to_storage<'a>(
        self: Box<Self>,
        storage_writer: &'a mut StorageWriter,
        _class_manager_client: &'a mut SharedClassManagerClient,
    ) -> BoxFuture<'a, Result<(), P2pSyncClientError>> {
        async move {
            storage_writer
                .begin_rw_txn()?
                .append_header(
                    self.block_header.block_header_without_hash.block_number,
                    &self.block_header,
                )?
                .append_block_signature(
                    self.block_header.block_header_without_hash.block_number,
                    self
                    .signatures
                    // In the future we will support multiple signatures.
                    .first()
                    // The verification that the size of the vector is 1 is done in the data
                    // verification.
                    .expect("Vec::first should return a value on a vector of size 1"),
                )?
                .commit()?;
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L82-123)
```rust
    fn parse_data_for_block<'a>(
        signed_headers_response_manager: &'a mut ClientResponsesManager<
            DataOrFin<SignedBlockHeader>,
        >,
        block_number: BlockNumber,
        _storage_reader: &'a StorageReader,
    ) -> BoxFuture<'a, Result<Option<Self::Output>, ParseDataError>> {
        async move {
            // TODO(noamsp): investigate and remove this timeout.
            let maybe_signed_header =
                timeout(Duration::from_secs(15), signed_headers_response_manager.next())
                    .await
                    .ok()
                    .flatten()
                    .ok_or(ParseDataError::BadPeer(BadPeerError::SessionEndedWithoutFin {
                        type_description: Self::TYPE_DESCRIPTION,
                    }))?;
            let Some(signed_block_header) = maybe_signed_header?.0 else {
                return Ok(None);
            };
            // TODO(shahak): Check that parent_hash is the same as the previous block's hash
            // and handle reverts.
            if block_number
                != signed_block_header.block_header.block_header_without_hash.block_number
            {
                return Err(ParseDataError::BadPeer(BadPeerError::HeadersUnordered {
                    expected_block_number: block_number,
                    actual_block_number: signed_block_header
                        .block_header
                        .block_header_without_hash
                        .block_number,
                }));
            }
            if signed_block_header.signatures.len() != ALLOWED_SIGNATURES_LENGTH {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongSignaturesLength {
                    signatures: signed_block_header.signatures,
                }));
            }
            Ok(Some(signed_block_header))
        }
        .boxed()
    }
```

**File:** crates/apollo_storage/src/header.rs (L469-476)
```rust
fn update_hash_mapping<'env>(
    txn: &DbTransaction<'env, RW>,
    block_hash_to_number_table: &'env BlockHashToNumberTable<'env>,
    block_header: &StorageBlockHeader,
    block_number: BlockNumber,
) -> Result<(), StorageError> {
    block_hash_to_number_table.insert(txn, &block_header.block_hash, &block_number)?;
    Ok(())
```

**File:** crates/apollo_central_sync/src/lib.rs (L476-477)
```rust
        // hash against the parent hash stored in the storage.
        self.verify_parent_block_hash(block_number, &block).await?;
```

**File:** crates/starknet_api/src/block.rs (L722-735)
```rust
pub fn verify_block_signature(
    sequencer_pub_key: &SequencerPublicKey,
    signature: &BlockSignature,
    state_diff_commitment: &GlobalRoot,
    block_hash: &BlockHash,
) -> Result<bool, BlockVerificationError> {
    let message_hash = Poseidon::hash_array(&[block_hash.0, state_diff_commitment.0]);
    verify_message_hash_signature(&message_hash, &signature.0, &sequencer_pub_key.0).map_err(
        |err| BlockVerificationError::BlockSignatureVerificationFailed {
            block_hash: *block_hash,
            error: err,
        },
    )
}
```

**File:** crates/apollo_p2p_sync/src/client/header_test.rs (L105-108)
```rust
                let txn = storage_reader.begin_ro_txn().unwrap();
                let block_header = txn.get_block_header(block_number).unwrap().unwrap();
                assert_eq!(block_number, block_header.block_header_without_hash.block_number);
                assert_eq!(*block_hash, block_header.block_hash);
```

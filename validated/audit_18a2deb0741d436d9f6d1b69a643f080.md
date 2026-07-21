### Title
Peer-controlled `protocol_version` in P2P `BlockHeadersResponse` accepted without block-hash verification, storing wrong `StarknetVersion` that gates `VersionedConstants` for RPC fee estimation and simulation — (`crates/apollo_protobuf/src/converters/header.rs`)

---

### Summary

The P2P sync client stores the `starknet_version` field received from a peer's `BlockHeadersResponse` directly into the DB without verifying it against the block hash. Because `starknet_version` is a committed field inside the block hash, a malicious peer can supply the canonical `block_hash` alongside a downgraded `protocol_version` (e.g., `"0.13.0"` for a block whose real version is `"0.14.3"`). The node accepts and persists the wrong version. All subsequent RPC calls that reference that block — `estimateFee`, `simulateTransactions`, `traceBlockTransactions` — read the wrong `StarknetVersion` from storage and select the wrong `VersionedConstants`, returning authoritative-looking but incorrect fee and execution results.

---

### Finding Description

**Deserialization — no semantic guard on `protocol_version`**

`TryFrom<protobuf::SignedBlockHeader> for SignedBlockHeader` at [1](#0-0) 
calls `StarknetVersion::try_from(value.protocol_version.to_owned())`. The `TryFrom<String>` implementation [2](#0-1) 
accepts any dot-separated string that matches a known enum variant. `"0.13.0"` parses to `StarknetVersion::V0_13_0` without error. There is no check that the parsed version is consistent with the `block_hash` field in the same message.

**P2P sync client — no block-hash or signature verification**

`parse_data_for_block` in the header stream builder [3](#0-2) 
performs only two checks: block-number ordering and signature-vector length. It does not:
- recompute the block hash from the received header fields and compare it to `block_hash`
- cryptographically verify the `BlockSignature` against the sequencer's public key

The `write_to_storage` path [4](#0-3) 
calls `append_header` unconditionally with whatever `starknet_version` was decoded from the peer's message.

**Storage — wrong version persisted**

`append_header` calls `update_starknet_version` [5](#0-4) 
which writes the peer-supplied `StarknetVersion` into the `starknet_version` table. [6](#0-5) 
The table uses run-length encoding: a single wrong entry propagates to all subsequent blocks until the version changes again.

**`starknet_version` is committed inside the block hash**

`calculate_block_hash` [7](#0-6) 
chains `starknet_version` as a Felt into the Poseidon hash. A peer that sends the real `block_hash` with a downgraded `protocol_version` produces an internally inconsistent header that the sync client never detects.

**RPC execution reads `starknet_version` to select `VersionedConstants`**

`VersionedConstants::get(&starknet_version)` returns version-specific constants. [8](#0-7) 
The test confirms concrete divergence: [9](#0-8) 
`invoke_tx_max_n_steps` is 3 000 000 for `V0_13_0` and 10 000 000 for `V0_13_2`. `OsConfigHashVersion` selection is also gated on `starknet_version`: [10](#0-9) 
A downgraded version silently switches the OS config hash algorithm from Blake (V4) to Pedersen (V3) for blocks at or above the `V0_14_3` cutover.

---

### Impact Explanation

A node syncing via P2P that connects to a single malicious peer will store a wrong `StarknetVersion` for every block that peer serves. Every call to `starknet_estimateFee`, `starknet_simulateTransactions`, or `starknet_traceBlockTransactions` against those blocks will execute under the wrong `VersionedConstants` — different step limits, different syscall gas costs, different feature flags (`enable_reverts`, `disable_cairo0_redeclaration`, etc.) — and return authoritative-looking but incorrect fee and execution results to all downstream clients.

---

### Likelihood Explanation

Any peer the node connects to can mount this attack. No cryptographic material, operator access, or special privilege is required. The attacker only needs to serve a syntactically valid `BlockHeadersResponse` with a known-valid but wrong `protocol_version` string. The real `block_hash` and `BlockSignature` can be copied verbatim from the canonical chain; the P2P sync client never verifies either.

---

### Recommendation

1. **Verify the block hash**: after deserializing a `SignedBlockHeader`, recompute `calculate_block_hash` from the received fields and assert it equals the received `block_hash`. This binds `starknet_version` to the hash and makes any tampering detectable.
2. **Verify the block signature**: call `verify_block_signature` against the known sequencer public key before writing to storage.
3. As a defence-in-depth measure, reject any `protocol_version` that is lower than the version of the immediately preceding stored block (versions must be non-decreasing).

---

### Proof of Concept

```rust
// In crates/apollo_protobuf/src/converters/header_test.rs (production-adjacent)
#[test]
fn peer_can_inject_wrong_starknet_version() {
    use crate::protobuf;
    use crate::sync::SignedBlockHeader;
    use starknet_api::block::StarknetVersion;

    // Build a minimal protobuf header with protocol_version = "0.13.0"
    // for a block whose canonical version should be "0.14.3".
    let proto_header = protobuf::SignedBlockHeader {
        protocol_version: "0.13.0".to_string(),
        // ... fill required fields with valid dummy values ...
        ..Default::default()
    };

    let signed: SignedBlockHeader = proto_header.try_into().unwrap();
    assert_eq!(
        signed.block_header.block_header_without_hash.starknet_version,
        StarknetVersion::V0_13_0,   // wrong version accepted and stored
    );
    // A subsequent VersionedConstants::get(&V0_13_0) returns 3M step limit
    // instead of the correct 10M+ limit for V0_14_3.
}
```

### Citations

**File:** crates/apollo_protobuf/src/converters/header.rs (L126-134)
```rust
        let starknet_version = match StarknetVersion::try_from(value.protocol_version.to_owned()) {
            Ok(version) => version,
            Err(_) => {
                return Err(ProtobufConversionError::OutOfRangeValue {
                    type_description: "starknet version",
                    value_as_str: value.protocol_version,
                });
            }
        };
```

**File:** crates/starknet_api/src/block.rs (L154-162)
```rust
impl TryFrom<String> for StarknetVersion {
    type Error = StarknetApiError;

    /// Parses a string separated by dots into a StarknetVersion.
    fn try_from(starknet_version: String) -> Result<Self, StarknetApiError> {
        let version: Vec<u8> =
            starknet_version.split('.').map(|x| x.parse::<u8>()).try_collect()?;
        Self::try_from(version)
    }
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

**File:** crates/apollo_storage/src/header.rs (L340-343)
```rust
        self.update_starknet_version(
            &block_number,
            &block_header.block_header_without_hash.starknet_version,
        )
```

**File:** crates/apollo_storage/src/header.rs (L347-362)
```rust
    fn update_starknet_version(
        self,
        block_number: &BlockNumber,
        starknet_version: &StarknetVersion,
    ) -> StorageResult<Self> {
        let starknet_version_table = self.open_table(&self.tables().starknet_version)?;
        let mut cursor = starknet_version_table.cursor(self.txn())?;
        cursor.lower_bound(block_number)?;
        let res = cursor.prev()?;

        match res {
            Some((_block_number, last_starknet_version))
                if last_starknet_version == *starknet_version => {}
            _ => starknet_version_table.insert(self.txn(), block_number, starknet_version)?,
        }
        Ok(self)
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L245-282)
```rust
pub fn calculate_block_hash(
    partial_block_hash_components: &PartialBlockHashComponents,
    state_root: GlobalRoot,
    previous_block_hash: BlockHash,
) -> StarknetApiResult<BlockHash> {
    let block_hash_version: BlockHashVersion =
        partial_block_hash_components.starknet_version.try_into()?;
    let block_commitments = &partial_block_hash_components.header_commitments;
    Ok(BlockHash(
        HashChain::new()
            .chain(&block_hash_version.clone().into())
            .chain(&partial_block_hash_components.block_number.0.into())
            .chain(&state_root.0)
            .chain(&partial_block_hash_components.sequencer.0)
            .chain(&partial_block_hash_components.timestamp.0.into())
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
            .chain(&block_commitments.event_commitment.0)
            .chain(&block_commitments.receipt_commitment.0)
            .chain_iter(
                gas_prices_to_hash(
                    &partial_block_hash_components.l1_gas_price,
                    &partial_block_hash_components.l1_data_gas_price,
                    &partial_block_hash_components.l2_gas_price,
                    &block_hash_version,
                )
                .iter(),
            )
            .chain(
                &Felt::try_from(&partial_block_hash_components.starknet_version)
                    .expect("Expect ASCII version"),
            )
            .chain(&Felt::ZERO)
            .chain(&previous_block_hash.0)
            .get_poseidon_hash(),
    ))
}
```

**File:** crates/blockifier/src/blockifier_versioned_constants.rs (L40-60)
```rust
define_versioned_constants!(
    VersionedConstants,
    RawVersionedConstants,
    VersionedConstantsError,
    StarknetVersion::V0_13_0,
    "resources/versioned_constants_diff_regression",
    (V0_13_0, "../resources/blockifier_versioned_constants_0_13_0.json"),
    (V0_13_1, "../resources/blockifier_versioned_constants_0_13_1.json"),
    (V0_13_1_1, "../resources/blockifier_versioned_constants_0_13_1_1.json"),
    (V0_13_2, "../resources/blockifier_versioned_constants_0_13_2.json"),
    (V0_13_2_1, "../resources/blockifier_versioned_constants_0_13_2_1.json"),
    (V0_13_3, "../resources/blockifier_versioned_constants_0_13_3.json"),
    (V0_13_4, "../resources/blockifier_versioned_constants_0_13_4.json"),
    (V0_13_5, "../resources/blockifier_versioned_constants_0_13_5.json"),
    (V0_13_6, "../resources/blockifier_versioned_constants_0_13_6.json"),
    (V0_14_0, "../resources/blockifier_versioned_constants_0_14_0.json"),
    (V0_14_1, "../resources/blockifier_versioned_constants_0_14_1.json"),
    (V0_14_2, "../resources/blockifier_versioned_constants_0_14_2.json"),
    (V0_14_3, "../resources/blockifier_versioned_constants_0_14_3.json"),
    (V0_14_4, "../resources/blockifier_versioned_constants_0_14_4.json"),
);
```

**File:** crates/apollo_rpc_execution/src/execution_test.rs (L839-851)
```rust
// Test that we retrieve the correct versioned constants.
#[test]
fn test_get_versioned_constants() {
    let starknet_version_13_0 = StarknetVersion::try_from("0.13.0".to_string()).unwrap();
    let starknet_version_13_1 = StarknetVersion::try_from("0.13.1".to_string()).unwrap();
    let starknet_version_13_2 = StarknetVersion::try_from("0.13.2".to_string()).unwrap();
    let versioned_constants = VersionedConstants::get(&starknet_version_13_0).unwrap();
    assert_eq!(versioned_constants.invoke_tx_max_n_steps, 3_000_000);
    let versioned_constants = VersionedConstants::get(&starknet_version_13_1).unwrap();
    assert_eq!(versioned_constants.invoke_tx_max_n_steps, 4_000_000);
    let versioned_constants = VersionedConstants::get(&starknet_version_13_2).unwrap();
    assert_eq!(versioned_constants.invoke_tx_max_n_steps, 10_000_000);
}
```

**File:** crates/starknet_api/src/core.rs (L126-129)
```rust
impl From<StarknetVersion> for OsConfigHashVersion {
    fn from(starknet_version: StarknetVersion) -> Self {
        if starknet_version < OS_CONFIG_HASH_V4_CUTOVER { Self::V3 } else { Self::V4 }
    }
```

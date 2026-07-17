### Title
Unauthenticated `ContractCodeResponse` (V1) Accepted Without Sender Verification Before `SignedContractCodeResponse` Activates — (`File: chain/client/src/stateless_validation/validate.rs`)

### Summary

Before protocol version 85 (`SignedContractCodeResponse`), any network peer can send a `ContractCodeResponse::V1` message to a chunk validator's `PartialWitnessActor`. The validator's `validate_contract_code_response` function skips all sender-identity and signature checks for V1 responses, accepting arbitrary contract code bytes from any peer. The accepted bytes are immediately stored as the contract code to be used during state-witness validation. This is the nearcore analog of the external report's "anyone can call `receiveCollateral()`" — a function that should only be callable by a privileged component (a legitimate chunk producer) can be called by any unprivileged network peer.

### Finding Description

`validate_contract_code_response` in `chain/client/src/stateless_validation/validate.rs` (lines 369–382) gates the signature check behind a protocol-version flag:

```rust
pub fn validate_contract_code_response(
    epoch_manager: &dyn EpochManagerAdapter,
    response: &ContractCodeResponse,
    store: &Store,
) -> Result<ChunkRelevance, Error> {
    let key = response.chunk_production_key();
    require_relevant!(validate_chunk_relevant(epoch_manager, key, store)?);
    let protocol_version = epoch_manager.get_epoch_protocol_version(&key.epoch_id)?;
    if ProtocolFeature::SignedContractCodeResponse.enabled(protocol_version) {
        validate_witness_contract_code_response_signature(epoch_manager, response)?;
    }
    Ok(ChunkRelevance::Relevant)
}
```

When `SignedContractCodeResponse` is **not** enabled (protocol version < 85), the only check performed is `validate_chunk_relevant` — which verifies that the `ChunkProductionKey` refers to a known, in-range chunk. It does **not** verify who sent the response. Any peer that knows a valid `ChunkProductionKey` (which is public information derivable from block headers) can craft a `ContractCodeResponse::V1` with arbitrary `compressed_contracts` bytes and send it to a validator node.

The handler in `partial_witness_actor.rs` (lines 1211–1224) then unconditionally decompresses and stores the received bytes:

```rust
fn handle_contract_code_response(&self, response: ContractCodeResponse) -> Result<(), Error> {
    if !validate_contract_code_response(...).is_relevant() {
        return Ok(());
    }
    let key = response.chunk_production_key().clone();
    let contracts = response.decompress_contracts()?;
    self.partial_witness_tracker.store_accessed_contract_codes(key, contracts)
}
```

The `ContractCodeResponse::V1` variant's `verify_signature` always returns `false` and carries no `responder` field — it is structurally unsigned. The `SignedContractCodeResponse` feature (protocol version 85) introduced `ContractCodeResponseV2` with a mandatory chunk-producer signature, but V1 remains parseable and accepted on pre-85 networks.

The exact divergent value: Borsh discriminant `0x00` (tag for `ContractCodeResponse::V1`) with attacker-controlled `compressed_contracts` bytes, accepted without any identity check.

### Impact Explanation

A malicious peer (no privileged keys required — only a TCP connection to a validator node) can:

1. Observe a valid `ChunkProductionKey` from any recent block header (public data).
2. Craft a `ContractCodeResponse::V1` with that key and arbitrary `compressed_contracts` content.
3. Send it to a chunk validator before the legitimate response arrives.
4. The validator stores the attacker-supplied bytes as the contract code for that chunk's validation.
5. When the validator attempts to compile and execute the injected bytes as WASM, it will either fail to compile (causing the validator to fail witness validation and withhold its endorsement) or, if the attacker crafts bytes that decompress to valid-but-wrong WASM, cause the validator to produce an incorrect endorsement.

The primary impact is **denial of chunk endorsement** for targeted validators: by poisoning the contract-code cache with garbage bytes, the attacker can prevent validators from endorsing chunks, degrading liveness. On a network running protocol version < 85, this is reachable by any peer that can establish a TCP connection to a validator.

### Likelihood Explanation

- **Attacker precondition**: Establish a TCP connection to a validator node (standard peer connection, no keys required) and know a valid `ChunkProductionKey` (derivable from public block headers).
- **Trigger**: Send a `ContractCodeResponse::V1` message with a valid key and malformed `compressed_contracts`.
- **Protocol version scope**: Affects all nodes running protocol version < 85 (pre-`SignedContractCodeResponse`). Protocol version 85 is a nightly feature (`ProtocolFeature::SignedContractCodeResponse => 85`), meaning mainnet nodes at stable versions below 85 are exposed.
- **No race condition required**: The attacker simply needs to send the poisoned response before the legitimate chunk producer responds.

### Recommendation

1. **Immediate**: Backport the `SignedContractCodeResponse` check to apply unconditionally to V1 responses as well — reject any `ContractCodeResponse` that lacks a verifiable chunk-producer signature, regardless of protocol version.
2. **Alternatively**: In `validate_contract_code_response`, reject `ContractCodeResponse::V1` entirely when the local node is running a protocol version that supports V2, treating an unsigned response as invalid.
3. **Long-term**: The `ContractCodeResponse::V1` variant should be removed from the accepted message set once all nodes have upgraded past protocol version 85.

### Proof of Concept

**Attacker steps (pre-protocol-version-85 network):**

1. Connect to a validator node as a peer (standard NEAR P2P handshake).
2. Read any recent block header to extract a valid `ChunkProductionKey` (`shard_id`, `epoch_id`, `height_created`).
3. Construct:
   ```
   ContractCodeResponse::V1(ContractCodeResponseV1 {
       next_chunk: <valid ChunkProductionKey>,
       compressed_contracts: <malformed/garbage compressed bytes>,
   })
   ```
   Borsh-serialized with discriminant `0x00`.
4. Send as a `PeerMessage::ContractCodeResponse` to the target validator.
5. The validator's `handle_contract_code_response` calls `validate_contract_code_response`, which passes (only `validate_chunk_relevant` runs — no signature check).
6. `response.decompress_contracts()` is called on the garbage bytes; if decompression fails, the handler returns an error and the validator's witness-validation pipeline stalls waiting for contract code that never validly arrives, causing it to miss the endorsement deadline.

**Relevant code path:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** chain/client/src/stateless_validation/validate.rs (L369-382)
```rust
pub fn validate_contract_code_response(
    epoch_manager: &dyn EpochManagerAdapter,
    response: &ContractCodeResponse,
    store: &Store,
) -> Result<ChunkRelevance, Error> {
    let key = response.chunk_production_key();
    require_relevant!(validate_chunk_relevant(epoch_manager, key, store)?);
    let protocol_version = epoch_manager.get_epoch_protocol_version(&key.epoch_id)?;
    if ProtocolFeature::SignedContractCodeResponse.enabled(protocol_version) {
        validate_witness_contract_code_response_signature(epoch_manager, response)?;
    }

    Ok(ChunkRelevance::Relevant)
}
```

**File:** chain/client/src/stateless_validation/partial_witness/partial_witness_actor.rs (L1211-1224)
```rust
    fn handle_contract_code_response(&self, response: ContractCodeResponse) -> Result<(), Error> {
        if !validate_contract_code_response(
            self.epoch_manager.as_ref(),
            &response,
            self.runtime.store(),
        )?
        .is_relevant()
        {
            return Ok(());
        }
        let key = response.chunk_production_key().clone();
        let contracts = response.decompress_contracts()?;
        self.partial_witness_tracker.store_accessed_contract_codes(key, contracts)
    }
```

**File:** core/primitives/src/stateless_validation/contract_distribution.rs (L372-421)
```rust
pub enum ContractCodeResponse {
    V1(ContractCodeResponseV1) = 0,
    V2(ContractCodeResponseV2) = 1,
}

impl ContractCodeResponse {
    pub fn encode(
        next_chunk: ChunkProductionKey,
        contracts: &Vec<CodeBytes>,
        signer: &ValidatorSigner,
        protocol_version: ProtocolVersion,
    ) -> std::io::Result<Self> {
        if ProtocolFeature::SignedContractCodeResponse.enabled(protocol_version) {
            ContractCodeResponseV2::encode(next_chunk, contracts, signer).map(Self::V2)
        } else {
            ContractCodeResponseV1::encode(next_chunk, contracts).map(Self::V1)
        }
    }

    pub fn chunk_production_key(&self) -> &ChunkProductionKey {
        match self {
            Self::V1(v1) => &v1.next_chunk,
            Self::V2(v2) => &v2.inner.next_chunk,
        }
    }

    /// Account that produced this response. Available only for signed variants.
    pub fn responder(&self) -> Option<&AccountId> {
        match self {
            Self::V1(_) => None,
            Self::V2(v2) => Some(&v2.inner.responder),
        }
    }

    pub fn decompress_contracts(&self) -> std::io::Result<Vec<CodeBytes>> {
        let compressed_contracts = match self {
            Self::V1(v1) => &v1.compressed_contracts,
            Self::V2(v2) => &v2.inner.compressed_contracts,
        };
        compressed_contracts.decode().map(|(data, _size)| data)
    }

    /// Verifies the signature for signed variants. Returns `false` for
    /// unsigned variants since there is nothing to verify.
    pub fn verify_signature(&self, public_key: &PublicKey) -> bool {
        match self {
            Self::V1(_) => false,
            Self::V2(v2) => v2.verify_signature(public_key),
        }
    }
```

**File:** core/primitives-core/src/version.rs (L434-446)
```rust
    ValidateBlockOrdinalAndEpochSyncDataHash,
    /// Authenticate `ContractCodeResponse` messages with a chunk-producer
    /// signature, matching the signed-message pattern already used by
    /// `ChunkContractAccesses` and `ContractCodeRequest`. Senders emit
    /// `ContractCodeResponseV2` (with a signed inner payload); receivers
    /// require a verifiable signature before processing the response.
    SignedContractCodeResponse,
    ClampOutgoingGasAdmission,
    /// Charge the contract-loading fee (and finalize as a gas-bearing abort
    /// rather than a zero-gas nop) when a compiled module fails to load at
    /// `Module::deserialize`.
    FixContractLoadingError,
}
```

**File:** core/primitives-core/src/version.rs (L565-573)
```rust
            | ProtocolFeature::ValidateBlockOrdinalAndEpochSyncDataHash
            | ProtocolFeature::YieldWithId
            | ProtocolFeature::ExecutionMetadataV4
            | ProtocolFeature::SignedContractCodeResponse
            | ProtocolFeature::ClampOutgoingGasAdmission
            | ProtocolFeature::AccountCostIncrease
            | ProtocolFeature::DelegateV2 => 85,

            ProtocolFeature::FixContractLoadingError => 86,
```

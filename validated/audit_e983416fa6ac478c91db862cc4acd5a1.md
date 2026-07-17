### Title
`ContractCodeResponse::V1` Accepted Without Authentication While Paired `ContractCodeRequest` Is Always Signed — (`core/primitives/src/stateless_validation/contract_distribution.rs`, `chain/client/src/stateless_validation/validate.rs`)

---

### Summary

The stateless-validation contract-distribution flow uses two paired messages: `ContractCodeRequest` (chunk validator → chunk producer) and `ContractCodeResponse` (chunk producer → chunk validator). `ContractCodeRequest` is **always** signed and its signature is **always** verified unconditionally. `ContractCodeResponse` carries no signature in its `V1` variant and its receiver skips all authentication before `ProtocolFeature::SignedContractCodeResponse` activates (protocol version 85). Any network peer that knows a valid `ChunkProductionKey` can inject a forged `ContractCodeResponse::V1` carrying arbitrary contract bytes into a chunk validator's witness-assembly cache, causing witness validation to fail for that validator.

---

### Finding Description

`ContractCodeRequest` is defined as a single-variant enum whose inner payload always carries a `signature_differentiator` and is always signed by the requester: [1](#0-0) 

The signature is verified unconditionally in `validate_contract_code_request`: [2](#0-1) 

`ContractCodeResponse`, by contrast, has two variants: `V1` (no signature, no `responder` field) and `V2` (signed inner payload): [3](#0-2) 

`ContractCodeResponseV1` carries only `next_chunk` and `compressed_contracts` — no signature, no identity field: [4](#0-3) 

The receiver's validation function gates authentication behind a protocol-version check: [5](#0-4) 

Before `SignedContractCodeResponse` (version 85) activates, the `if` branch is never entered. A `ContractCodeResponse::V1` message (Borsh discriminant `0x00`) passes `validate_chunk_relevant` (which only checks that the `ChunkProductionKey` is within the valid height window — public information) and is immediately stored: [6](#0-5) 

The `ContractCodeResponse::encode` constructor emits `V1` when the feature is not yet active: [7](#0-6) 

The asymmetry is structural: `ContractCodeRequest` uses the signed-message pattern unconditionally; `ContractCodeResponse` does not use it until a separate feature gate activates. This is the direct nearcore analog of the external report's pattern — one component in a paired protocol flow uses the versioned/authenticated form while the other does not.

---

### Impact Explanation

Before protocol version 85, any network peer that can deliver a `ContractCodeResponse::V1` message to a chunk validator (routed via `T1MessageBody::ContractCodeResponse`) can inject arbitrary compressed contract bytes into the validator's `PartialEncodedStateWitnessTracker`. The injected bytes are stored under the target `ChunkProductionKey`. When the validator subsequently assembles the witness, the injected code hashes will not match the hashes committed in the `ChunkContractAccesses` message, causing witness validation to fail. The validator will not endorse the chunk. If multiple validators are targeted simultaneously, the chunk may fail to accumulate sufficient endorsements, causing a liveness degradation for that shard.

There is no retry path for contract code requests once the `processed_contract_code_requests` LRU cache records the key: [8](#0-7) 

A forged response arriving before the legitimate one permanently poisons the cache entry for that `(ChunkProductionKey, AccountId)` pair.

---

### Likelihood Explanation

The `ChunkProductionKey` (shard ID, epoch ID, height) is fully public — it is broadcast in block headers and chunk headers. Any peer connected to the network can construct a valid-looking `ContractCodeResponse::V1` for any in-flight chunk. The message is routed as a `T1MessageBody` (high-priority tier), so it is not rate-limited more aggressively than legitimate traffic. The attack window is the duration of a single chunk's validation round (a few seconds), which is sufficient for a well-positioned peer to race the legitimate response.

---

### Recommendation

Apply the same unconditional signed-message pattern to `ContractCodeResponse` that is already applied to `ContractCodeRequest`. Specifically:

1. Remove the `ProtocolFeature::SignedContractCodeResponse` conditional in `validate_contract_code_response` and always call `validate_witness_contract_code_response_signature`.
2. Remove `ContractCodeResponse::V1` from the enum (or add a version-gate that drops V1 messages the same way `EarlyKickout` drops wrong-version witness parts), so that unsigned responses are never stored.
3. Align the emit side: `ContractCodeResponse::encode` should always produce `V2`.

The pattern already established for `ChunkContractAccesses` (always signed, V1/V2 gated by `EarlyKickout`) and `ContractCodeRequest` (always signed, single variant) should be the template.

---

### Proof of Concept

**Divergent Borsh bytes:** A forged `ContractCodeResponse::V1` begins with discriminant byte `0x00`, followed by a Borsh-encoded `ChunkProductionKey` (shard_id u64 LE + epoch_id [u8;32] + height_created u64 LE) for any currently-active chunk, followed by any valid zstd-compressed byte sequence. No signature field is present or checked.

**Call path (pre-version-85 network):**

```
peer sends PeerMessage::Routed → T1MessageBody::ContractCodeResponse(V1{...})
  → PartialWitnessActor::handle_contract_code_response
    → validate_contract_code_response          // only checks height window
      // ProtocolFeature::SignedContractCodeResponse.enabled() == false
      // signature branch NOT entered
    → partial_witness_tracker.store_accessed_contract_codes(key, forged_contracts)
      // forged bytes stored; legitimate response (if it arrives later) is ignored
      // because the key is already populated
  → witness assembly uses forged contracts
  → hash mismatch against ChunkContractAccesses commitment
  → witness validation fails; chunk not endorsed by this validator
``` [5](#0-4) [3](#0-2) [9](#0-8)

### Citations

**File:** core/primitives/src/stateless_validation/contract_distribution.rs (L260-302)
```rust
pub enum ContractCodeRequest {
    V1(ContractCodeRequestV1) = 0,
}

impl ContractCodeRequest {
    pub fn new(
        next_chunk: ChunkProductionKey,
        contracts: HashSet<CodeHash>,
        main_transition: MainTransitionKey,
        signer: &ValidatorSigner,
    ) -> Self {
        Self::V1(ContractCodeRequestV1::new(next_chunk, contracts, main_transition, signer))
    }

    pub fn requester(&self) -> &AccountId {
        match self {
            Self::V1(request) => &request.inner.requester,
        }
    }

    pub fn contracts(&self) -> &[CodeHash] {
        match self {
            Self::V1(request) => &request.inner.contracts,
        }
    }

    pub fn chunk_production_key(&self) -> &ChunkProductionKey {
        match self {
            Self::V1(request) => &request.inner.next_chunk,
        }
    }

    pub fn main_transition(&self) -> &MainTransitionKey {
        match self {
            Self::V1(request) => &request.inner.main_transition,
        }
    }

    pub fn verify_signature(&self, public_key: &PublicKey) -> bool {
        match self {
            Self::V1(v1) => v1.verify_signature(public_key),
        }
    }
```

**File:** core/primitives/src/stateless_validation/contract_distribution.rs (L372-422)
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
}
```

**File:** core/primitives/src/stateless_validation/contract_distribution.rs (L424-440)
```rust
#[derive(Debug, Clone, PartialEq, Eq, BorshSerialize, BorshDeserialize, ProtocolSchema)]
pub struct ContractCodeResponseV1 {
    // The same as `next_chunk` in `ContractCodeRequest`
    next_chunk: ChunkProductionKey,
    /// Code for the contracts.
    compressed_contracts: CompressedContractCode,
}

impl ContractCodeResponseV1 {
    pub fn encode(
        next_chunk: ChunkProductionKey,
        contracts: &Vec<CodeBytes>,
    ) -> std::io::Result<Self> {
        let (compressed_contracts, _size) = CompressedContractCode::encode(contracts)?;
        Ok(Self { next_chunk, compressed_contracts })
    }
}
```

**File:** chain/client/src/stateless_validation/validate.rs (L352-367)
```rust
pub fn validate_contract_code_request(
    epoch_manager: &dyn EpochManagerAdapter,
    request: &ContractCodeRequest,
    store: &Store,
) -> Result<ChunkRelevance, Error> {
    let key = request.chunk_production_key();
    require_relevant!(validate_chunk_relevant_as_validator(
        epoch_manager,
        key,
        request.requester(),
        store
    )?);
    validate_witness_contract_code_request_signature(epoch_manager, request)?;

    Ok(ChunkRelevance::Relevant)
}
```

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

**File:** chain/client/src/stateless_validation/partial_witness/partial_witness_actor.rs (L1120-1130)
```rust
        let key = request.chunk_production_key();
        let processed_requests_key = (key.clone(), request.requester().clone());
        if self.processed_contract_code_requests.contains(&processed_requests_key) {
            tracing::warn!(
                target: "client",
                ?processed_requests_key,
                "contract code request from this account was already processed"
            );
            return Ok(());
        }
        self.processed_contract_code_requests.push(processed_requests_key, ());
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

**File:** core/primitives-core/src/version.rs (L435-440)
```rust
    /// Authenticate `ContractCodeResponse` messages with a chunk-producer
    /// signature, matching the signed-message pattern already used by
    /// `ChunkContractAccesses` and `ContractCodeRequest`. Senders emit
    /// `ContractCodeResponseV2` (with a signed inner payload); receivers
    /// require a verifiable signature before processing the response.
    SignedContractCodeResponse,
```

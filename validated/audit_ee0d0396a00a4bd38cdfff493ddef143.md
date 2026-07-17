### Title
Unsigned `SpiceContractCodeResponse` Allows Any Peer to Inject Unauthenticated Contract Bytecode into SPICE Chunk Validators — (`File: core/primitives/src/stateless_validation/contract_distribution.rs`)

---

### Summary

The SPICE path's `SpiceContractCodeResponse` message carries no signature and no sender identity field. Any network peer can send a `SpiceContractCodeResponse` to a SPICE chunk validator; the validator's `handle_spice_contract_code_response` processes the payload — including an expensive decompression step — without verifying that the response originated from a legitimate chunk producer. This is the direct nearcore analog of the external report's "kfrags are never validated / Alice's signature is optional / no protection from replay attacks."

---

### Finding Description

The non-Spice `ContractCodeResponse` enum was upgraded from an unsigned V1 to a signed V2 under `ProtocolFeature::SignedContractCodeResponse` (protocol version 85). The signed variant (`ContractCodeResponseV2`) carries an `inner` payload plus a `signature` field; `validate_contract_code_response` enforces the signature once the feature is active. [1](#0-0) [2](#0-1) 

The SPICE path's equivalent, `SpiceContractCodeResponse`, has only a single variant `V1` (`SpiceContractCodeResponseV1`) that contains only `chunk_id` and `compressed_contracts` — **no signature field, no responder identity**. [3](#0-2) 

`handle_spice_contract_code_response` in `SpiceChunkValidatorActor` calls `response.decompress_contracts()` immediately and then resolves the received bytes against pending chunk data, with no authentication step whatsoever: [4](#0-3) 

By contrast, the SPICE *request* path (`SpiceContractCodeRequest`) is properly signed and the data distributor verifies the signature before responding: [5](#0-4) [6](#0-5) 

The asymmetry is exact: requests are authenticated; responses are not.

---

### Impact Explanation

**Decompression-based resource exhaustion (DoS).** Any peer on the network can send a `SpiceContractCodeResponse` with a maximally-sized compressed payload. The validator calls `decompress_contracts()` unconditionally before any hash check. A single malicious peer can repeatedly trigger expensive decompression on every SPICE chunk validator it can reach, stalling chunk validation and degrading liveness.

**No sender attribution.** Because there is no `responder` field and no signature, the validator cannot determine which chunk producer sent the response. It cannot penalise or ignore a misbehaving sender, and cannot distinguish a legitimate response from a spoofed one. This mirrors the external report's "Alice cannot tell if this is taking place, and Ursula cannot distinguish legitimate policies from fake ones."

**Hash check does not substitute for authentication.** The code computes `CodeHash(hash(&contract.0))` from received bytes and matches against `trusted.missing`. This prevents substitution of wrong contract bytes (preimage resistance), but it does not prevent the DoS vector above, and it does not authenticate the sender.

---

### Likelihood Explanation

The `SpiceContractCodeResponseMessage` is dispatched directly from the network layer to the actor with no prior filtering: [7](#0-6) [8](#0-7) 

Any peer that can establish a network connection to a SPICE chunk validator can send this message. No validator role, no privileged position, and no knowledge of secret material is required. The `SpiceContractCodeResponse::encode` constructor is public and takes only a `SpiceChunkId` (observable from the network) and arbitrary contract bytes. [9](#0-8) 

---

### Recommendation

1. **Add a signature to `SpiceContractCodeResponse`** following the exact pattern used by `ContractCodeResponseV2`: introduce a `SpiceContractCodeResponseV2` with an `inner` struct (containing `chunk_id`, `responder: AccountId`, `compressed_contracts`, and a `signature_differentiator`) plus a `signature` field signed by the responding chunk producer.

2. **Verify the signature in `handle_spice_contract_code_response`** before calling `decompress_contracts()`, checking that `responder` is a chunk producer for the relevant shard/epoch, mirroring `validate_witness_contract_code_response_signature`. [10](#0-9) 

3. **Move decompression after authentication** so that unauthenticated peers cannot trigger the decompression cost at all.

---

### Proof of Concept

```
1. Observe a SpiceChunkContractAccesses message on the wire to learn the target
   SpiceChunkId and the set of expected CodeHash values.

2. Construct a SpiceContractCodeResponse::V1 with:
   - chunk_id = observed SpiceChunkId
   - compressed_contracts = a maximally-large valid zstd-compressed payload
     (content irrelevant; hashes will not match trusted.missing, but
      decompress_contracts() runs before any hash check)

3. Send this message directly to any SPICE chunk validator peer.

4. The validator calls response.decompress_contracts() unconditionally,
   allocating and decompressing up to MAX_UNCOMPRESSED_CONTRACT_CODE_RESPONSE_SIZE
   bytes of attacker-controlled data.

5. Repeat at high frequency from one or more peers to exhaust validator
   CPU/memory and delay or prevent chunk validation for the targeted shard.
``` [11](#0-10)

### Citations

**File:** core/primitives/src/stateless_validation/contract_distribution.rs (L442-464)
```rust
#[derive(Debug, Clone, PartialEq, Eq, BorshSerialize, BorshDeserialize, ProtocolSchema)]
pub struct ContractCodeResponseV2 {
    inner: ContractCodeResponseV2Inner,
    /// Signature of the inner, signed by the responder.
    signature: Signature,
}

impl ContractCodeResponseV2 {
    pub fn encode(
        next_chunk: ChunkProductionKey,
        contracts: &Vec<CodeBytes>,
        signer: &ValidatorSigner,
    ) -> std::io::Result<Self> {
        let inner =
            ContractCodeResponseV2Inner::encode(next_chunk, contracts, signer.validator_id())?;
        let signature = signer.sign_bytes(&borsh::to_vec(&inner).unwrap());
        Ok(Self { inner, signature })
    }

    fn verify_signature(&self, public_key: &PublicKey) -> bool {
        self.signature.verify(&borsh::to_vec(&self.inner).unwrap(), public_key)
    }
}
```

**File:** core/primitives/src/stateless_validation/contract_distribution.rs (L494-496)
```rust
/// Represents max allowed size of the raw (not compressed) contract code response,
/// corresponds to the size of borsh-serialized ContractCodeResponse.
pub const MAX_UNCOMPRESSED_CONTRACT_CODE_RESPONSE_SIZE: u64 =
```

**File:** core/primitives/src/stateless_validation/contract_distribution.rs (L889-928)
```rust
/// Message from a SPICE chunk validator to a chunk producer requesting missing contract code.
#[derive(Debug, Clone, PartialEq, Eq, BorshSerialize, BorshDeserialize, ProtocolSchema)]
pub struct SpiceContractCodeRequest {
    inner: SpiceContractCodeRequestInner,
    signature: Signature,
}

impl SpiceContractCodeRequest {
    pub fn new(
        chunk_id: SpiceChunkId,
        contracts: HashSet<CodeHash>,
        signer: &ValidatorSigner,
    ) -> Self {
        assert!(
            contracts.len() <= MAX_CONTRACTS_PER_REQUEST,
            "too many contracts in request: {} > {}",
            contracts.len(),
            MAX_CONTRACTS_PER_REQUEST,
        );
        let inner =
            SpiceContractCodeRequestInner::new(signer.validator_id().clone(), chunk_id, contracts);
        let signature = signer.sign_bytes(&borsh::to_vec(&inner).unwrap());
        Self { inner, signature }
    }

    pub fn requester(&self) -> &AccountId {
        &self.inner.requester
    }

    pub fn chunk_id(&self) -> &SpiceChunkId {
        &self.inner.chunk_id
    }

    pub fn contracts(&self) -> &[CodeHash] {
        &self.inner.contracts
    }

    pub fn verify_signature(&self, public_key: &PublicKey) -> bool {
        self.signature.verify(&borsh::to_vec(&self.inner).unwrap(), public_key)
    }
```

**File:** core/primitives/src/stateless_validation/contract_distribution.rs (L950-988)
```rust
/// Response from a chunk producer to a SPICE chunk validator with the requested contract code.
#[derive(Debug, Clone, PartialEq, Eq, BorshSerialize, BorshDeserialize, ProtocolSchema)]
#[borsh(use_discriminant = true)]
#[repr(u8)]
pub enum SpiceContractCodeResponse {
    V1(SpiceContractCodeResponseV1) = 0,
}

impl SpiceContractCodeResponse {
    pub fn encode(chunk_id: SpiceChunkId, contracts: &Vec<CodeBytes>) -> std::io::Result<Self> {
        SpiceContractCodeResponseV1::encode(chunk_id, contracts).map(|v1| Self::V1(v1))
    }

    pub fn chunk_id(&self) -> &SpiceChunkId {
        match self {
            Self::V1(v1) => &v1.chunk_id,
        }
    }

    pub fn decompress_contracts(&self) -> std::io::Result<Vec<CodeBytes>> {
        let compressed_contracts = match self {
            Self::V1(v1) => &v1.compressed_contracts,
        };
        compressed_contracts.decode().map(|(data, _size)| data)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, BorshSerialize, BorshDeserialize, ProtocolSchema)]
pub struct SpiceContractCodeResponseV1 {
    chunk_id: SpiceChunkId,
    compressed_contracts: CompressedContractCode,
}

impl SpiceContractCodeResponseV1 {
    pub fn encode(chunk_id: SpiceChunkId, contracts: &Vec<CodeBytes>) -> std::io::Result<Self> {
        let (compressed_contracts, _size) = CompressedContractCode::encode(contracts)?;
        Ok(Self { chunk_id, compressed_contracts })
    }
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

**File:** chain/client/src/stateless_validation/validate.rs (L546-569)
```rust
fn validate_witness_contract_code_response_signature(
    epoch_manager: &dyn EpochManagerAdapter,
    response: &ContractCodeResponse,
) -> Result<(), Error> {
    let Some(responder) = response.responder() else {
        return Err(Error::Other(
            "Unsigned contract code response in epoch where signature is required".to_owned(),
        ));
    };
    let key = response.chunk_production_key();
    let chunk_producers =
        epoch_manager.get_epoch_chunk_producers_for_shard(&key.epoch_id, key.shard_id)?;
    if !chunk_producers.contains(responder) {
        return Err(Error::Other(format!(
            "Contract code response responder {responder} is not a chunk producer for shard {} in epoch {:?}",
            key.shard_id, key.epoch_id,
        )));
    }
    let validator = epoch_manager.get_validator_by_account_id(&key.epoch_id, responder)?;
    if !response.verify_signature(validator.public_key()) {
        return Err(Error::Other("Invalid witness contract code response signature".to_owned()));
    }
    Ok(())
}
```

**File:** chain/client/src/spice/chunk_validator_actor.rs (L218-227)
```rust
impl Handler<SpiceContractCodeResponseMessage> for SpiceChunkValidatorActor {
    fn handle(
        &mut self,
        SpiceContractCodeResponseMessage(response): SpiceContractCodeResponseMessage,
    ) {
        if let Err(err) = self.handle_spice_contract_code_response(response) {
            tracing::error!(target: "spice_chunk_validator", ?err, "error handling contract code response");
        }
    }
}
```

**File:** chain/client/src/spice/chunk_validator_actor.rs (L603-643)
```rust
    fn handle_spice_contract_code_response(
        &mut self,
        response: SpiceContractCodeResponse,
    ) -> Result<(), Error> {
        let contracts = response.decompress_contracts()?;

        // Map code_hash -> bytes for the received contracts.
        let mut received: HashMap<CodeHash, CodeBytes> = HashMap::new();
        for contract in contracts {
            let hash = CodeHash(hash(&contract.0));
            received.insert(hash, contract);
        }

        // Resolve across all pending chunks that are waiting on any of these contracts.
        let mut maybe_ready: Vec<SpiceChunkId> = Vec::new();
        for (chunk_id, entry) in &mut self.partial_chunk_data {
            let Some(trusted) = &mut entry.trusted else {
                continue;
            };

            let mut resolved_any = false;
            for (hash, bytes) in &received {
                if trusted.missing.remove(hash) {
                    entry.contracts.push(bytes.clone());
                    resolved_any = true;
                }
            }
            if resolved_any && trusted.missing.is_empty() {
                maybe_ready.push(chunk_id.clone());
            }
        }

        let signer = self
            .validator_signer
            .get()
            .ok_or_else(|| Error::NotAValidator("no signer".to_owned()))?;
        for chunk_id in maybe_ready {
            self.try_assemble_and_validate_chunk(&chunk_id, signer.clone())?;
        }
        Ok(())
    }
```

**File:** chain/client/src/spice/data_distributor_actor.rs (L1114-1124)
```rust
        // Verify request signature before any other checks to prevent cache pollution.
        let validator = self.epoch_manager.get_validator_by_account_id(epoch_id, &requester)?;
        if !request.verify_signature(validator.public_key()) {
            tracing::warn!(
                target: "spice_data_distribution",
                ?chunk_id,
                ?requester,
                "invalid contract code request signature"
            );
            return Ok(());
        }
```

**File:** chain/network/src/spice/data_distribution.rs (L26-36)
```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SpiceContractCodeResponseMessage(pub SpiceContractCodeResponse);

#[derive(Clone, MultiSend, MultiSenderFrom)]
pub struct SpiceDataDistributorSenderForNetwork {
    pub incoming: Sender<SpiceIncomingPartialData>,
    pub request: Sender<SpicePartialDataRequest>,
    pub contract_accesses: Sender<SpiceChunkContractAccessesMessage>,
    pub contract_code_request: Sender<SpiceContractCodeRequestMessage>,
    pub contract_code_response: Sender<SpiceContractCodeResponseMessage>,
}
```

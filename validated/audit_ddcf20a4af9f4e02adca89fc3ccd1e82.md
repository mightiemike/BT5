### Title
Unauthenticated `ContractCodeResponse` Accepted Before `SignedContractCodeResponse` Feature Activation — (`File: chain/client/src/stateless_validation/validate.rs`)

---

### Summary

`validate_contract_code_response` skips all sender-identity and signature checks when the `SignedContractCodeResponse` protocol feature is not yet active. Any network peer can forge a `ContractCodeResponse` carrying arbitrary contract bytecode, have it accepted by a chunk validator, and cause chunk-state-witness validation to fail — disrupting endorsement and block production.

---

### Finding Description

`validate_contract_code_response` is the sole gate that chunk validators apply to incoming `ContractCodeResponse` messages before storing the contained contract code in the partial-witness tracker.

```rust
// chain/client/src/stateless_validation/validate.rs  lines 369-382
pub fn validate_contract_code_response(
    epoch_manager: &dyn EpochManagerAdapter,
    response: &ContractCodeResponse,
    store: &Store,
) -> Result<ChunkRelevance, Error> {
    let key = response.chunk_production_key();
    require_relevant!(validate_chunk_relevant(epoch_manager, key, store)?);   // ← timing/epoch only
    let protocol_version = epoch_manager.get_epoch_protocol_version(&key.epoch_id)?;
    if ProtocolFeature::SignedContractCodeResponse.enabled(protocol_version) {
        validate_witness_contract_code_response_signature(epoch_manager, response)?;
    }
    Ok(ChunkRelevance::Relevant)
}
``` [1](#0-0) 

Before `SignedContractCodeResponse` is active, the only check performed is `validate_chunk_relevant`, which verifies:
- `shard_id` is in the epoch's shard layout
- `height_created` is within the `(final_head, head + MAX_HEIGHTS_AHEAD]` window
- `epoch_id` is plausible for the current tip [2](#0-1) 

It does **not** check who sent the response or whether the sender is a chunk producer for the relevant shard. Compare this to every other stateless-validation message:

| Message | Sender check |
|---|---|
| `validate_partial_encoded_state_witness` | `validate_chunk_relevant_as_validator` + signature |
| `validate_chunk_endorsement` | `validate_chunk_relevant_as_validator` + signature |
| `validate_chunk_contract_accesses` | `validate_chunk_relevant_as_validator` + signature |
| `validate_contract_code_request` | `validate_chunk_relevant_as_validator` + signature |
| **`validate_contract_code_response`** | **`validate_chunk_relevant` only (no sender, no signature)** | [3](#0-2) 

The signed variant (`ContractCodeResponseV2`) carries a `responder` field and a chunk-producer signature, but this is only enforced after `SignedContractCodeResponse` (protocol version 85) is active. [4](#0-3) 

The unsigned `ContractCodeResponseV1` exposes `responder() → None`, so there is literally no identity to verify even if the caller tried. [5](#0-4) 

After passing `validate_contract_code_response`, the caller unconditionally stores the contracts:

```rust
// chain/client/src/stateless_validation/partial_witness/partial_witness_actor.rs
fn handle_contract_code_response(&self, response: ContractCodeResponse) -> Result<(), Error> {
    if !validate_contract_code_response(...)?. is_relevant() { return Ok(()); }
    let key = response.chunk_production_key().clone();
    let contracts = response.decompress_contracts()?;
    self.partial_witness_tracker.store_accessed_contract_codes(key, contracts)
}
``` [6](#0-5) 

---

### Impact Explanation

An attacker who can send P2P messages (any network peer, not just a validator) can:

1. Observe a `ContractCodeRequest` routed from a chunk validator to a chunk producer (or simply guess the `ChunkProductionKey` from public chain data).
2. Craft a `ContractCodeResponseV1` with a valid `ChunkProductionKey` (correct `epoch_id`, `shard_id`, `height_created`) but **wrong contract bytecode**.
3. Send it to the target chunk validator before the legitimate response arrives.
4. The chunk validator stores the forged bytecode, re-executes the chunk state witness against it, obtains a wrong post-state root, and refuses to endorse the chunk.
5. If enough validators are targeted, the chunk fails to accumulate sufficient endorsement stake, and the block producer cannot include the chunk — stalling shard progress.

This is a **liveness attack** reachable by any unprivileged network peer, not just a validator.

---

### Likelihood Explanation

- `SignedContractCodeResponse` is a nightly/upcoming feature at protocol version 85; it is not yet active on mainnet.
- The attack requires only the ability to send routed P2P messages, which any connected peer can do.
- The `ChunkProductionKey` (`epoch_id`, `shard_id`, `height_created`) is fully public from block headers.
- The attacker does not need stake, a validator key, or any privileged position.

---

### Recommendation

Apply the same pattern used by every other stateless-validation message: call `validate_chunk_relevant_as_validator` (which calls `ensure_chunk_validator`) **and** verify the sender's signature unconditionally, not only after a feature flag.

Concretely, `validate_contract_code_response` should:

1. Verify that `response.responder()` is a chunk producer for `key.shard_id` in `key.epoch_id` (mirroring `validate_witness_contract_code_response_signature`).
2. Verify the signature against that producer's public key.
3. Do this regardless of `SignedContractCodeResponse` activation — or, at minimum, reject unsigned `V1` responses entirely once the feature is active and ensure `V1` cannot be sent on the wire post-activation (analogous to the `witness_version_mismatch` gate for partial witnesses). [7](#0-6) [8](#0-7) 

---

### Proof of Concept

```
Attacker (any peer)                    Chunk Validator (CV)
        |                                      |
        |   [observes ChunkProductionKey K     |
        |    from public block headers]        |
        |                                      |
        |  ContractCodeResponseV1 {            |
        |    next_chunk: K,                    |
        |    compressed_contracts: <garbage>   |
        |  }  ─────────────────────────────>  |
        |                                      |
        |                          validate_contract_code_response(K):
        |                            validate_chunk_relevant(K) → Relevant  ✓
        |                            SignedContractCodeResponse not active → skip sig check
        |                          store_accessed_contract_codes(K, <garbage>)
        |                                      |
        |                          [legitimate response arrives later — ignored
        |                           or overwrites with correct code, but race
        |                           window exists]
        |                                      |
        |                          validate_chunk_state_witness:
        |                            re-execute with <garbage> code
        |                            post_state_root ≠ expected → FAIL
        |                            chunk not endorsed
```

The exact divergent value is the `compressed_contracts` field of `ContractCodeResponseV1`: any byte sequence that decompresses to syntactically valid but semantically wrong WASM will produce a wrong post-state root, causing `validate_chunk_state_witness_impl` to return `Error::InvalidChunkStateWitness`. [9](#0-8)

### Citations

**File:** chain/client/src/stateless_validation/validate.rs (L306-382)
```rust
/// Function to validate the chunk endorsement. In addition of ChunkProductionKey, we check the following:
/// - signature of endorsement and metadata is valid
pub fn validate_chunk_endorsement(
    epoch_manager: &dyn EpochManagerAdapter,
    endorsement: &ChunkEndorsement,
    store: &Store,
) -> Result<ChunkRelevance, Error> {
    let _span = tracing::debug_span!(
        target: "stateless_validation",
        "validate_chunk_endorsement",
        height = endorsement.chunk_production_key().height_created,
        shard_id = %endorsement.chunk_production_key().shard_id,
        validator = %endorsement.account_id(),
        tag_block_production = true
    )
    .entered();

    require_relevant!(validate_chunk_relevant_as_validator(
        epoch_manager,
        &endorsement.chunk_production_key(),
        endorsement.account_id(),
        store,
    )?);
    validate_chunk_endorsement_signature(epoch_manager, endorsement)?;

    Ok(ChunkRelevance::Relevant)
}

pub fn validate_chunk_contract_accesses(
    epoch_manager: &dyn EpochManagerAdapter,
    accesses: &ChunkContractAccesses,
    signer: &ValidatorSigner,
    store: &Store,
) -> Result<ChunkRelevance, Error> {
    let key = accesses.chunk_production_key();
    require_relevant!(validate_chunk_relevant_as_validator(
        epoch_manager,
        key,
        signer.validator_id(),
        store
    )?);
    validate_witness_contract_accesses_signature(epoch_manager, accesses, store)?;

    Ok(ChunkRelevance::Relevant)
}

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

**File:** chain/client/src/stateless_validation/validate.rs (L420-490)
```rust
fn validate_chunk_relevant(
    epoch_manager: &dyn EpochManagerAdapter,
    chunk_production_key: &ChunkProductionKey,
    store: &Store,
) -> Result<ChunkRelevance, Error> {
    let shard_id = chunk_production_key.shard_id;
    let epoch_id = chunk_production_key.epoch_id;
    let height_created = chunk_production_key.height_created;

    if !epoch_manager.get_shard_layout(&epoch_id)?.shard_ids().contains(&shard_id) {
        tracing::error!(
            target: "stateless_validation",
            ?chunk_production_key,
            "shard id is not in the shard layout of the epoch"
        );
        return Err(Error::InvalidShardId(shard_id));
    }

    // TODO(https://github.com/near/nearcore/issues/11301): replace these direct DB accesses with messages
    // sent to the client actor. for a draft, see https://github.com/near/nearcore/commit/e186dc7c0b467294034c60758fe555c78a31ef2d
    let head = store.get_ser::<Tip>(DBCol::BlockMisc, HEAD_KEY);
    let final_head = store.get_ser::<Tip>(DBCol::BlockMisc, FINAL_HEAD_KEY);

    // Avoid processing state witness for old chunks.
    // In particular it is impossible for a chunk created at a height
    // that doesn't exceed the height of the current final block to be
    // included in the chain. This addresses both network-delayed messages
    // as well as malicious behavior of a chunk producer.
    if let Some(final_head) = final_head {
        if height_created <= final_head.height {
            tracing::debug!(
                target: "stateless_validation",
                ?chunk_production_key,
                final_head_height = final_head.height,
                "skipping because height created is not greater than final head height",
            );
            return Ok(ChunkRelevance::TooLate);
        }
    }
    if let Some(head) = head {
        if height_created > head.height + MAX_HEIGHTS_AHEAD {
            tracing::debug!(
                target: "stateless_validation",
                ?chunk_production_key,
                head_height = head.height,
                %MAX_HEIGHTS_AHEAD,
                "skipping because height created is more than max heights ahead blocks ahead of head height"
            );
            return Ok(ChunkRelevance::TooEarly);
        }

        // Try to find the EpochId to which this witness will belong based on its height.
        // It's not always possible to determine the exact epoch_id because the exact
        // starting height of the next epoch isn't known until it actually starts,
        // so things can get unclear around epoch boundaries.
        // Let's collect the epoch_ids in which the witness might possibly be.
        let possible_epochs =
            epoch_manager.possible_epochs_of_height_around_tip(&head, height_created)?;
        if !possible_epochs.contains(&epoch_id) {
            tracing::debug!(
                target: "stateless_validation",
                ?chunk_production_key,
                ?possible_epochs,
                "skipping because epoch id is not in the possible list of epochs"
            );
            return Ok(ChunkRelevance::UnknownEpochId);
        }
    }

    Ok(ChunkRelevance::Relevant)
}
```

**File:** chain/client/src/stateless_validation/validate.rs (L546-568)
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

**File:** chain/client/src/stateless_validation/partial_witness/partial_witness_actor.rs (L72-85)
```rust
pub(super) fn version_mismatch(version: Option<ProtocolVersion>, is_v2: bool) -> bool {
    let Some(version) = version else {
        return false;
    };
    is_v2 != ProtocolFeature::EarlyKickout.enabled(version)
}

/// Same check as [`version_mismatch`], for a partial state witness.
pub(super) fn witness_version_mismatch(
    version: Option<ProtocolVersion>,
    witness: &VersionedPartialEncodedStateWitness,
) -> bool {
    version_mismatch(version, matches!(witness, VersionedPartialEncodedStateWitness::V2(_)))
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

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L622-631)
```rust
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
```

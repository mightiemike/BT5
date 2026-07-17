### Title
Unsigned `ContractCodeResponse::V1` Accepted Without Sender Authentication Across the `SignedContractCodeResponse` Protocol-Version Boundary — (File: `chain/client/src/stateless_validation/validate.rs`)

---

### Summary

`validate_contract_code_response` skips all sender-identity checks when the epoch's protocol version is below 85 (`SignedContractCodeResponse` not yet active). Any network peer can craft a `ContractCodeResponse::V1` (Borsh discriminant `0x00`) carrying arbitrary contract bytes for any valid `ChunkProductionKey` and have it accepted and stored by a chunk validator as if it originated from the legitimate chunk producer. This is the direct nearcore analog of the mail-server impersonation: the sender identity is not cryptographically bound to the message.

---

### Finding Description

`ContractCodeResponse` is a versioned Borsh enum with two variants:

- `V1(ContractCodeResponseV1)` — discriminant `0x00`, **no signature, no responder field**.
- `V2(ContractCodeResponseV2)` — discriminant `0x01`, carries a `responder: AccountId` and a `Signature` over the Borsh-serialized inner payload. [1](#0-0) 

The receiver-side gate in `validate_contract_code_response` is:

```rust
let protocol_version = epoch_manager.get_epoch_protocol_version(&key.epoch_id)?;
if ProtocolFeature::SignedContractCodeResponse.enabled(protocol_version) {
    validate_witness_contract_code_response_signature(epoch_manager, response)?;
}
``` [2](#0-1) 

`SignedContractCodeResponse` activates at protocol version 85. [3](#0-2) 

When the epoch's protocol version is 84 or lower, the entire signature-verification branch is skipped. `ContractCodeResponse::V1` carries no `responder` field (`response.responder()` returns `None`), so there is no identity to verify even if the caller wanted to. The only check that runs is `validate_chunk_relevant`, which only confirms the `ChunkProductionKey` is within a plausible height range — information that is fully public. [4](#0-3) 

After passing validation, the contracts are stored unconditionally:

```rust
let contracts = response.decompress_contracts()?;
self.partial_witness_tracker.store_accessed_contract_codes(key, contracts)
``` [5](#0-4) 

The `ContractCodeResponseV1` struct has no sender field at all: [6](#0-5) 

The signed path (`V2`) does bind the responder identity and verifies it against the epoch's chunk-producer set: [7](#0-6) 

But that path is unreachable when the epoch protocol version is < 85.

---

### Impact Explanation

During any epoch whose protocol version is 84 or lower (i.e., the upgrade window from version 84 → 85), any network peer — without validator credentials — can:

1. Observe a valid `ChunkProductionKey` from the public P2P gossip (e.g., from a `ContractCodeRequest`).
2. Craft a `ContractCodeResponse::V1` (Borsh: `[0x00 || next_chunk_borsh || compressed_garbage]`) with arbitrary `compressed_contracts` bytes.
3. Send it directly to a chunk validator node.
4. The validator accepts and stores the arbitrary bytes as the contract code for that chunk.
5. When the validator attempts to validate the chunk, it cannot find the expected contracts (the stored bytes hash to wrong `CodeHash` values), causing chunk validation to fail.
6. Repeated injection across multiple validators can prevent a chunk from accumulating sufficient endorsements, causing the chunk to be missed and disrupting network liveness.

The attacker controls the exact Borsh bytes of the `compressed_contracts` field — the divergent value is the `ContractCodeResponse::V1` discriminant `0x00` combined with attacker-chosen payload — and the validator has no mechanism to distinguish this from a legitimate chunk-producer response.

---

### Likelihood Explanation

- **Barrier**: Any node that can establish a P2P connection to the network can send this message. No validator key or stake is required.
- **Window**: The attack is active during any epoch whose protocol version is < 85. On a network upgrading from 84 → 85, this window spans at least one epoch (≈12 hours on mainnet). Networks that have not yet reached version 85 are permanently exposed.
- **Observability**: The `ChunkProductionKey` (shard_id, epoch_id, height_created) is broadcast publicly via `ContractCodeRequest` messages, giving the attacker the exact key to target.

---

### Recommendation

Reject `ContractCodeResponse::V1` unconditionally once `SignedContractCodeResponse` is the minimum supported protocol version, or add a version-independent check that rejects any response whose `responder()` returns `None`. The existing `validate_witness_contract_code_response_signature` already handles the V1 case correctly (it returns an error for unsigned responses) — the fix is to remove the `if ProtocolFeature::SignedContractCodeResponse.enabled(protocol_version)` guard and always call it, or to add an explicit rejection of the V1 Borsh variant at the deserialization boundary once version 85 is the floor.

---

### Proof of Concept

```
// Attacker observes a ContractCodeRequest on the P2P network, extracting:
//   next_chunk = ChunkProductionKey { shard_id: S, epoch_id: E, height_created: H }
//   (epoch E has protocol_version = 84)

// Attacker crafts ContractCodeResponse::V1 in Borsh:
//   [0x00]                          <- V1 discriminant
//   [borsh(next_chunk)]             <- valid ChunkProductionKey (public)
//   [borsh(CompressedContractCode)] <- arbitrary garbage bytes

// Attacker sends this to a chunk validator node over the P2P layer.
// validate_contract_code_response runs:
//   - validate_chunk_relevant: PASSES (key is valid)
//   - SignedContractCodeResponse.enabled(84) == false -> signature check SKIPPED
//   -> response accepted, garbage stored as contract code for chunk (S, E, H)

// Chunk validator later tries to validate the chunk:
//   - Looks up contracts by CodeHash -> stored bytes hash to wrong values
//   - Expected contracts not found -> chunk validation fails
//   - Chunk endorsement not produced -> chunk may be missed
```

### Citations

**File:** core/primitives/src/stateless_validation/contract_distribution.rs (L369-375)
```rust
#[derive(Debug, Clone, PartialEq, Eq, BorshSerialize, BorshDeserialize, ProtocolSchema)]
#[borsh(use_discriminant = true)]
#[repr(u8)]
pub enum ContractCodeResponse {
    V1(ContractCodeResponseV1) = 0,
    V2(ContractCodeResponseV2) = 1,
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

**File:** core/primitives-core/src/version.rs (L568-571)
```rust
            | ProtocolFeature::SignedContractCodeResponse
            | ProtocolFeature::ClampOutgoingGasAdmission
            | ProtocolFeature::AccountCostIncrease
            | ProtocolFeature::DelegateV2 => 85,
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

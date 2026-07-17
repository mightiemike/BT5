### Title
Unauthenticated `ContractCodeResponse::V1` Accepted Without Sender Verification at Protocol Upgrade Boundary — (`chain/client/src/stateless_validation/validate.rs`)

### Summary

`validate_contract_code_response()` skips all sender authentication when `ProtocolFeature::SignedContractCodeResponse` is not yet enabled. Any peer on the network can send a `ContractCodeResponse::V1` carrying arbitrary contract bytes for any valid `ChunkProductionKey`. The receiving validator stores those bytes unconditionally, poisoning its contract cache and causing stateless chunk validation to fail.

### Finding Description

`validate_contract_code_response()` gates the entire authentication step behind a protocol-version check:

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
``` [1](#0-0) 

Before protocol version 85, the `if` branch is never entered. The only check performed is `validate_chunk_relevant`, which verifies that the `ChunkProductionKey` is within a plausible height range — information that is entirely public. No check is made on who sent the message.

`ContractCodeResponse::V1` carries no `responder` field and no signature:

```rust
pub struct ContractCodeResponseV1 {
    next_chunk: ChunkProductionKey,
    compressed_contracts: CompressedContractCode,
}
``` [2](#0-1) 

`ContractCodeResponse::verify_signature()` returns `false` for V1 without error — it is simply a no-op:

```rust
pub fn verify_signature(&self, public_key: &PublicKey) -> bool {
    match self {
        Self::V1(_) => false,
        Self::V2(v2) => v2.verify_signature(public_key),
    }
}
``` [3](#0-2) 

The handler in `PartialWitnessActor` passes the response directly to storage after the unauthenticated validation:

```rust
fn handle_contract_code_response(&self, response: ContractCodeResponse) -> Result<(), Error> {
    if !validate_contract_code_response(...)?
        .is_relevant()
    {
        return Ok(());
    }
    let key = response.chunk_production_key().clone();
    let contracts = response.decompress_contracts()?;
    self.partial_witness_tracker.store_accessed_contract_codes(key, contracts)
}
``` [4](#0-3) 

The attacker-controlled bytes are stored under the legitimate `ChunkProductionKey`. When the validator later attempts to compile the contracts needed for chunk state witness validation, it retrieves these poisoned bytes. Because the bytes do not match the code hashes committed in the state witness, the validator cannot compile the required contracts and fails to produce a chunk endorsement.

The structural parallel to the external report is exact: the external router calls `setMerkleRoots()` directly, bypassing EVC so the authorization check sees the router's address rather than the real caller. Here, `validate_contract_code_response()` calls the signature check only conditionally, so pre-feature the "caller identity" (the chunk producer who should be the only authorized responder) is never verified at all — any peer fills that role.

### Impact Explanation

A network peer at protocol version 84 (the minimum supported version, `MIN_SUPPORTED_PROTOCOL_VERSION = 84`) can:

1. Observe a `ContractCodeRequest` broadcast by a chunk validator (public network traffic).
2. Craft a `ContractCodeResponse::V1` with the same `ChunkProductionKey` but with wrong or malformed compressed contract bytes.
3. Send it to the target validator before the legitimate chunk producer responds.
4. The validator stores the poisoned bytes, fails to match them against the expected code hashes from the state witness, and cannot endorse the chunk.

If enough validators are targeted, the chunk accumulates insufficient endorsements and the block producer cannot include it, stalling the shard. This is a targeted, repeatable DoS on stateless validation with no authentication barrier. [5](#0-4) 

### Likelihood Explanation

- The `ChunkProductionKey` (shard id, epoch id, height) is broadcast publicly and trivially observable.
- `ContractCodeResponse::V1` is a valid Borsh-serialized message accepted by any node whose epoch is at protocol version < 85.
- No stake, no validator role, and no cryptographic material is required to craft and send the message.
- The attack window is the time between the validator broadcasting a `ContractCodeRequest` and the legitimate chunk producer responding — typically a few hundred milliseconds, easily won by a co-located attacker.
- `MIN_SUPPORTED_PROTOCOL_VERSION = 84` means the vulnerable code path is compiled into and executed by the current binary for any epoch at that version. [6](#0-5) 

### Recommendation

The `SignedContractCodeResponse` feature (protocol version 85) is the intended fix. To harden the upgrade boundary, `validate_contract_code_response` should additionally reject `ContractCodeResponse::V1` (discriminant `0`) outright when the receiving node's compiled `PROTOCOL_VERSION` already includes `SignedContractCodeResponse`. This prevents a downgrade-variant attack where a peer deliberately sends a V1 message to a post-85 node whose epoch has not yet crossed the boundary. [7](#0-6) 

### Proof of Concept

```
Attacker (any peer, no stake required):

1. Listen for ContractCodeRequest broadcast by validator V for
   ChunkProductionKey { shard_id: 0, epoch_id: E, height_created: H }
   where epoch E is at protocol_version < 85.

2. Craft ContractCodeResponse::V1 {
       next_chunk: ChunkProductionKey { shard_id: 0, epoch_id: E, height_created: H },
       compressed_contracts: <lz4-compressed garbage bytes>,
   }
   Borsh discriminant = 0x00 (V1).

3. Send directly to validator V via the P2P network.

4. validate_contract_code_response() passes:
   - validate_chunk_relevant() → Ok (key is plausible)
   - SignedContractCodeResponse.enabled(protocol_version < 85) → false → skip signature check
   → returns ChunkRelevance::Relevant

5. handle_contract_code_response() calls
   store_accessed_contract_codes(key, <garbage bytes>).

6. Validator V later tries to compile contracts for the chunk state witness.
   The garbage bytes do not match any expected CodeHash.
   Validator V fails to validate the chunk and does not produce an endorsement.

7. Repeat for each chunk height to sustain the DoS.
```

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

**File:** core/primitives/src/stateless_validation/contract_distribution.rs (L372-389)
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
```

**File:** core/primitives/src/stateless_validation/contract_distribution.rs (L414-421)
```rust
    /// Verifies the signature for signed variants. Returns `false` for
    /// unsigned variants since there is nothing to verify.
    pub fn verify_signature(&self, public_key: &PublicKey) -> bool {
        match self {
            Self::V1(_) => false,
            Self::V2(v2) => v2.verify_signature(public_key),
        }
    }
```

**File:** core/primitives/src/stateless_validation/contract_distribution.rs (L424-430)
```rust
#[derive(Debug, Clone, PartialEq, Eq, BorshSerialize, BorshDeserialize, ProtocolSchema)]
pub struct ContractCodeResponseV1 {
    // The same as `next_chunk` in `ContractCodeRequest`
    next_chunk: ChunkProductionKey,
    /// Code for the contracts.
    compressed_contracts: CompressedContractCode,
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

**File:** core/primitives-core/src/version.rs (L554-571)
```rust
            ProtocolFeature::_DeprecatedWasmtime => 84,
            ProtocolFeature::FixDelegateActionDepositWithFunctionCallError
            | ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
            | ProtocolFeature::FixDelegatedDeterministicStateInit
            | ProtocolFeature::GasKeys
            | ProtocolFeature::ContinuousEpochSync
            | ProtocolFeature::DynamicResharding
            | ProtocolFeature::StickyReshardingValidatorAssignment
            | ProtocolFeature::StrictNonce
            | ProtocolFeature::PostQuantumSignatures
            | ProtocolFeature::UniqueChunkTransactions
            | ProtocolFeature::ValidateBlockOrdinalAndEpochSyncDataHash
            | ProtocolFeature::YieldWithId
            | ProtocolFeature::ExecutionMetadataV4
            | ProtocolFeature::SignedContractCodeResponse
            | ProtocolFeature::ClampOutgoingGasAdmission
            | ProtocolFeature::AccountCostIncrease
            | ProtocolFeature::DelegateV2 => 85,
```

**File:** core/primitives-core/src/version.rs (L596-597)
```rust
/// Minimum supported protocol version for the current binary
pub const MIN_SUPPORTED_PROTOCOL_VERSION: ProtocolVersion = 84;
```

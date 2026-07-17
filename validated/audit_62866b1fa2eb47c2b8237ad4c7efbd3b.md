### Title
Unsigned `ContractCodeResponse::V2` Accepted Without Signature Verification Before `SignedContractCodeResponse` Feature Activation — (File: `chain/client/src/stateless_validation/validate.rs`)

### Summary

`validate_contract_code_response` enforces a signature check only when `ProtocolFeature::SignedContractCodeResponse` is enabled, but it never rejects a `ContractCodeResponse::V2` (the signed variant) when the feature is **not** enabled. Any unprivileged network peer can craft and send a `ContractCodeResponse::V2` carrying arbitrary contract bytes before the feature activates; the receiver accepts it without any authentication and stores the injected code into its compiled-contract cache.

### Finding Description

`ContractCodeResponse` is a Borsh-tagged enum with two variants:

- `V1` (discriminant `0`) — unsigned, used before `SignedContractCodeResponse`
- `V2` (discriminant `1`) — carries a chunk-producer signature, used after activation [1](#0-0) 

The receive-side gate in `validate_contract_code_response` is:

```rust
if ProtocolFeature::SignedContractCodeResponse.enabled(protocol_version) {
    validate_witness_contract_code_response_signature(epoch_manager, response)?;
}
Ok(ChunkRelevance::Relevant)
``` [2](#0-1) 

When the feature **is** enabled, a V1 response is correctly rejected because `response.responder()` returns `None` and `validate_witness_contract_code_response_signature` returns an error: [3](#0-2) 

But when the feature is **not** enabled, the `if` branch is skipped entirely. A V2 response (Borsh discriminant `1`) passes `validate_chunk_relevant`, skips all authentication, and is returned as `ChunkRelevance::Relevant`. The caller then decompresses and stores the attacker-supplied contract bytes: [4](#0-3) 

This is the exact invariant gap the external report describes: the function returns a "proceed" result without checking the complementary condition. For every other versioned stateless-validation message (`VersionedPartialEncodedStateWitness`, `ChunkContractAccesses`, `PartialEncodedContractDeploys`), a symmetric version gate exists that drops the wrong-version variant in both directions: [5](#0-4) 

No equivalent gate exists for `ContractCodeResponse`.

### Impact Explanation

A chunk validator's compiled-contract cache is populated from accepted `ContractCodeResponse` messages. If an attacker injects a V2 response carrying wrong contract bytes for a code hash that the validator is about to use, the validator will compile and cache the wrong code. When it subsequently validates the state witness for that chunk, it will execute the wrong contract, producing a divergent state root. This causes the validator to either reject a valid witness (missed endorsement, potential slashing) or accept an invalid one (consensus safety violation). The attack requires only a network connection to the target validator and knowledge of the current `ChunkProductionKey` (epoch, shard, height — all public).

### Likelihood Explanation

The attack window is the entire period before `SignedContractCodeResponse` is activated on mainnet. Any peer connected to a chunk validator can send the message. The `ChunkProductionKey` is derivable from public chain state. No privileged role is required.

### Recommendation

Add a symmetric version gate for `ContractCodeResponse`, mirroring the `version_mismatch` pattern used for witnesses and contract-distribution messages. Concretely, in `validate_contract_code_response`, after resolving `protocol_version`, reject a V2 response when the feature is not enabled:

```rust
let is_v2 = matches!(response, ContractCodeResponse::V2(_));
let feature_on = ProtocolFeature::SignedContractCodeResponse.enabled(protocol_version);
if is_v2 != feature_on {
    // Wrong variant for this epoch — drop silently (same pattern as version_mismatch).
    return Ok(ChunkRelevance::UnknownEpochId); // or a dedicated "WrongVersion" variant
}
if feature_on {
    validate_witness_contract_code_response_signature(epoch_manager, response)?;
}
```

This makes the invariant "V1 iff feature off, V2 iff feature on" explicit and enforced in both directions, closing the gap.

### Proof of Concept

1. Observe the current epoch's `ChunkProductionKey` (epoch_id, shard_id, height_created) from any RPC node — all public.
2. Before `SignedContractCodeResponse` is activated, craft:
   ```
   ContractCodeResponse::V2(ContractCodeResponseV2 {
       inner: ContractCodeResponseV2Inner {
           next_chunk: <target ChunkProductionKey>,
           responder: <any valid chunk-producer account id>,
           compressed_contracts: <attacker-chosen contract bytes, compressed>,
           signature_differentiator: "ContractCodeResponseV2Inner",
       },
       signature: <garbage — never checked>,
   })
   ```
   Borsh-serialize with discriminant `1`.
3. Send the serialized bytes to a chunk validator as a `ContractCodeResponseMessage` over the P2P network.
4. `validate_contract_code_response` resolves the epoch version, finds `SignedContractCodeResponse` not enabled, skips the `if` branch, and returns `Ok(Relevant)`.
5. `handle_contract_code_response` calls `response.decompress_contracts()` and `store_accessed_contract_codes`, caching the attacker's bytes under the target code hash.
6. When the validator processes the state witness for that chunk, it compiles and executes the injected code, producing a divergent result. [2](#0-1) [6](#0-5)

### Citations

**File:** core/primitives/src/stateless_validation/contract_distribution.rs (L369-389)
```rust
#[derive(Debug, Clone, PartialEq, Eq, BorshSerialize, BorshDeserialize, ProtocolSchema)]
#[borsh(use_discriminant = true)]
#[repr(u8)]
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

**File:** chain/client/src/stateless_validation/validate.rs (L546-554)
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
```

**File:** chain/client/src/stateless_validation/partial_witness/partial_witness_actor.rs (L67-77)
```rust
/// Returns true if a message's version is wrong for its epoch and should be dropped:
/// a V2 message before EarlyKickout is active, or a V1 message at or after it. If we
/// cannot resolve the version (for example during header sync), returns false: this gate
/// only rejects a known-wrong version, so an unknown one is left for downstream validation
/// to accept or drop. This matters for V2, whose parts are never retransmitted.
pub(super) fn version_mismatch(version: Option<ProtocolVersion>, is_v2: bool) -> bool {
    let Some(version) = version else {
        return false;
    };
    is_v2 != ProtocolFeature::EarlyKickout.enabled(version)
}
```

**File:** chain/client/src/stateless_validation/partial_witness/partial_witness_actor.rs (L1210-1224)
```rust
    /// Handles contract code responses message from chunk producer.
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

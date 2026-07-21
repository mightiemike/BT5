### Title
Gateway Admits Transactions with Deprecated Proof Versions That Blockifier Rejects at Pre-Validation - (File: crates/apollo_gateway/src/stateless_transaction_validator.rs)

### Summary
The gateway's stateless validator gates client-side proving with a single boolean flag (`allow_client_side_proving`) and never inspects the proof version embedded in `proof_facts`. The blockifier's pre-validation stage checks the proof version against the per-block `allowed_proof_versions` list from the versioned constants. Starting with Starknet v0.14.3, `allowed_proof_versions` contains only `PROOF_VERSION_V1` (`0x50524f4f4631`); `PROOF_VERSION_V0` (`0x50524f4f4630`) was removed. Any user can submit an Invoke V3 transaction carrying V0 proof facts, have it admitted by the gateway, and have it rejected by the blockifier — a version/config boundary mismatch that is the direct sequencer analog of the cross-chain quorum-increase bug.

### Finding Description

**Gateway admission check (coarse-grained):**

`StatelessTransactionValidator::validate_client_side_proving_allowed` returns `Ok(())` immediately when `allow_client_side_proving == true`, without inspecting the proof version at all.

```rust
fn validate_client_side_proving_allowed(
    &self,
    tx: &RpcInvokeTransaction,
) -> StatelessTransactionValidatorResult<()> {
    if self.config.allow_client_side_proving {
        return Ok(());   // ← no proof-version check
    }
    ...
}
``` [1](#0-0) 

The full `validate` entry point calls only `validate_client_side_proving_allowed` and `validate_proof_facts_and_proof_consistency` (which only checks that both fields are empty or both non-empty). Neither call parses or validates the proof version. [2](#0-1) 

**Blockifier pre-validation check (fine-grained, version-gated):**

`AccountTransaction::validate_proof_facts` reads `allowed_proof_versions` from the *current block's* versioned constants and rejects any proof whose version is not in that list:

```rust
if !os_constants.allowed_proof_versions.contains(&snos_proof_facts.proof_version.as_felt())
{
    return Err(TransactionPreValidationError::InvalidProofFacts(format!(
        "Proof version {} is not allowed under this protocol version.",
        snos_proof_facts.proof_version
    )));
}
``` [3](#0-2) 

**Versioned-constants boundary — V0 removed in v0.14.3:**

| Version | `allowed_proof_versions` |
|---------|--------------------------|
| v0.14.2 | `["0x50524f4f4630"]` (V0 only) |
| v0.14.3 | `["0x50524f4f4631"]` (V1 only) |
| v0.14.4 | `["0x50524f4f4631"]` (V1 only) | [4](#0-3) [5](#0-4) [6](#0-5) 

The `ProofVersion` enum retains V0 for historical re-execution, and `ProofFactsVariant::try_from` parses V0 successfully — so the proof facts are never rejected at the parsing layer, only at the version-allowlist check inside the blockifier. [7](#0-6) [8](#0-7) 

### Impact Explanation

When the sequencer produces blocks under v0.14.3 or v0.14.4, `allowed_proof_versions` contains only V1. A user who submits an Invoke V3 transaction with `proof_facts[0] = PROOF_VERSION_V0` (`0x50524f4f4630`) will:

1. Pass gateway stateless validation (only `allow_client_side_proving` is checked).
2. Enter the mempool and be propagated to peers.
3. Be selected for block inclusion by the batcher.
4. Fail `perform_pre_validation_stage` inside the blockifier with `InvalidProofFacts("Proof version … is not allowed under this protocol version.")`.
5. Be dropped without execution — the user's transaction is silently discarded.

This matches the allowed impact: **High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

### Likelihood Explanation

- The default production config has `allow_client_side_proving: true`.
- Any user running a client that generates V0 proof facts (e.g., a client built against v0.14.2 tooling) will hit this silently.
- No privileged action is required; the trigger is a normal user transaction submission.
- The divergent value (`PROOF_VERSION_V0 = 0x50524f4f4630`) is a well-known constant, making deliberate or accidental triggering straightforward.

### Recommendation

The gateway's stateless validator should parse the proof facts and validate the proof version against the current versioned constants' `allowed_proof_versions` list, mirroring the blockifier check. Concretely, `validate_client_side_proving_allowed` (or a new sibling function) should:

1. Call `ProofFactsVariant::try_from(&tx.proof_facts)` to parse the proof version.
2. Check that the parsed `proof_version.as_felt()` is contained in `VersionedConstants::latest_constants().os_constants.allowed_proof_versions`.
3. Return `Err(StatelessTransactionValidatorError::UnsupportedProofVersion { … })` if not.

This closes the version/config boundary gap between gateway admission and blockifier execution, analogous to caching the quorum requirement at message initiation in the cross-chain bug.

### Proof of Concept

```
Sequencer running Starknet v0.14.3
  gateway_config.static_config.stateless_tx_validator_config.allow_client_side_proving = true

User constructs RpcInvokeTransactionV3 with:
  proof_facts = [
    0x50524f4f4630,   // PROOF_VERSION_V0  ← deprecated since v0.14.3
    VIRTUAL_SNOS,
    <program_hash>,
    VIRTUAL_OS_OUTPUT_VERSION,
    <block_number>,
    <block_hash>,
    <config_hash>,
  ]
  proof = <any non-empty proof bytes>

Step 1 – Gateway stateless validation:
  validate_client_side_proving_allowed → Ok(())   // allow_client_side_proving=true, no version check
  validate_proof_facts_and_proof_consistency → Ok(())  // both non-empty
  → Transaction ADMITTED to mempool

Step 2 – Blockifier pre-validation (block built under v0.14.3 VC):
  validate_proof_facts:
    allowed_proof_versions = ["0x50524f4f4631"]   // V1 only
    snos_proof_facts.proof_version = V0
    → "Proof version … is not allowed under this protocol version."
    → TransactionPreValidationError::InvalidProofFacts
  → Transaction REJECTED, never executed, user tx silently lost
```

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L45-48)
```rust
        if let RpcTransaction::Invoke(invoke_tx) = tx {
            self.validate_client_side_proving_allowed(invoke_tx)?;
            self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
        }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L231-247)
```rust
    fn validate_client_side_proving_allowed(
        &self,
        tx: &RpcInvokeTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if self.config.allow_client_side_proving {
            return Ok(());
        }

        // Reject V3 transactions with proofs when client-side proving is disabled.
        let RpcInvokeTransaction::V3(tx) = tx;
        let has_proof_data = !tx.proof_facts.is_empty() || !tx.proof.is_empty();
        if has_proof_data {
            return Err(StatelessTransactionValidatorError::ClientSideProvingNotAllowed);
        }

        Ok(())
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L312-320)
```rust
        let os_constants = &block_context.versioned_constants.os_constants;

        if !os_constants.allowed_proof_versions.contains(&snos_proof_facts.proof_version.as_felt())
        {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Proof version {} is not allowed under this protocol version.",
                snos_proof_facts.proof_version
            )));
        }
```

**File:** crates/blockifier/resources/versioned_constants_diff_regression/0.14.2_0.14.3.txt (L1-3)
```text
~ /enable_casm_hash_migration: true
~ /os_constants/allowed_proof_versions/0: "0x50524f4f4631"
~ /os_constants/allowed_virtual_os_program_hashes/0: "0x53f6c9fcfd31d27279ff7d7e422b44623550a732b59fe193354a7316a96daa1"
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_14_3.json (L132-134)
```json
        "allowed_proof_versions": [
            "0x50524f4f4631"
        ],
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_14_4.json (L133-135)
```json
        "allowed_proof_versions": [
            "0x50524f4f4631"
        ],
```

**File:** crates/starknet_api/src/transaction/fields.rs (L638-653)
```rust
pub const PROOF_VERSION_V0: Felt = Felt::from_hex_unchecked("0x50524f4f4630");

// Represent the `PROOF_VERSION_V1` marker as a Felt ('PROOF1').
pub const PROOF_VERSION_V1: Felt = Felt::from_hex_unchecked("0x50524f4f4631");

/// Supported proof-facts version markers.
///
/// V0 is retained only so that historical blocks carrying V0 proof facts can be replayed (e.g. via
/// reexecution). Whether V0 is accepted is gated per protocol version in the blockifier; the proof
/// verifier no longer supports it.
#[cfg_attr(any(test, feature = "testing"), derive(EnumIter))]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProofVersion {
    V0,
    V1,
}
```

**File:** crates/starknet_api/src/transaction/fields.rs (L740-748)
```rust
        // Validate that the first element is a supported proof version marker.
        let proof_version = ProofVersion::try_from(*proof_version).map_err(|()| {
            StarknetApiError::InvalidProofFacts(format!(
                "Expected first field to be {} or {}, but got {}",
                ProofVersion::V0,
                ProofVersion::V1,
                proof_version,
            ))
        })?;
```

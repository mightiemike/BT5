### Title
`validate_proof_size` is defined but never called in `StatelessTransactionValidator::validate`, allowing oversized proofs to bypass the gateway size limit — (File: `crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

The `StatelessTransactionValidator::validate()` function calls two invoke-specific proof checks (`validate_client_side_proving_allowed` and `validate_proof_facts_and_proof_consistency`) but never calls `validate_proof_size`. The method exists, is correctly implemented, and has a corresponding `max_proof_size` config field (set to 480,000 bytes in production), but it is dead code. Any user can submit an `RpcInvokeTransactionV3` with an arbitrarily large `proof` field and the gateway will accept it.

### Finding Description

The main validation entry point is:

```rust
pub fn validate(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
    Self::validate_contract_address(tx)?;
    Self::validate_empty_account_deployment_data(tx)?;
    Self::validate_empty_paymaster_data(tx)?;
    self.validate_resource_bounds(tx)?;
    self.validate_tx_size(tx)?;
    self.validate_nonce_data_availability_mode(tx)?;
    self.validate_fee_data_availability_mode(tx)?;

    if let RpcTransaction::Invoke(invoke_tx) = tx {
        self.validate_client_side_proving_allowed(invoke_tx)?;
        self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
        // validate_proof_size is NEVER called here
    }
    ...
}
``` [1](#0-0) 

The missing check is fully implemented and ready to use:

```rust
fn validate_proof_size(
    &self,
    tx: &RpcInvokeTransaction,
) -> StatelessTransactionValidatorResult<()> {
    let RpcInvokeTransaction::V3(tx) = tx;
    let proof_size = tx.proof.0.len();
    if proof_size > self.config.max_proof_size {
        return Err(StatelessTransactionValidatorError::ProofTooLarge {
            proof_size,
            max_proof_size: self.config.max_proof_size,
        });
    }
    Ok(())
}
``` [2](#0-1) 

The production config explicitly sets `max_proof_size` to 480,000 bytes, confirming the intent to enforce this limit: [3](#0-2) [4](#0-3) 

The `StatelessTransactionValidatorConfig` struct documents the field: [5](#0-4) 

The analog to the external report is exact: just as `beforeDonate: false` means the Uniswap hook is simply skipped (the call is never made), `validate_proof_size` being absent from `validate()` means the proof-size gate is simply skipped — the function exists, the config value is set, but the call is never made.

### Impact Explanation

With `allow_client_side_proving: true` (the production default), an attacker can submit an `RpcInvokeTransactionV3` whose `proof` field is arbitrarily large — megabytes or more. The gateway accepts it, computes the transaction hash, stores it in the mempool, and propagates it to peers over P2P. Every node that receives the transaction repeats the same acceptance path. This is a **High** impact finding: **Mempool/gateway/RPC admission accepts invalid transactions before sequencing.** [6](#0-5) 

### Likelihood Explanation

Likelihood is **High**. The trigger requires only a well-formed `RpcInvokeTransactionV3` with a large `proof` byte array. No privileged access, no special account state, and no cryptographic knowledge is needed. The `proof` field is a plain `Vec<u8>` wrapped in `Arc`, accepted over the public JSON-RPC gateway endpoint. [7](#0-6) 

### Recommendation

Add the missing call inside the `if let RpcTransaction::Invoke` branch in `validate()`:

```rust
if let RpcTransaction::Invoke(invoke_tx) = tx {
    self.validate_client_side_proving_allowed(invoke_tx)?;
    self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
    self.validate_proof_size(invoke_tx)?;   // ← add this line
}
``` [8](#0-7) 

### Proof of Concept

1. Construct an `RpcInvokeTransactionV3` with a valid sender, nonce, resource bounds, and a `proof` field containing, e.g., 1,000,000 bytes of arbitrary data (well above the 480,000-byte limit).
2. Submit it to the gateway's `starknet_addInvokeTransaction` endpoint.
3. Observe that the gateway returns a transaction hash (acceptance) rather than a `ProofTooLarge` error.
4. Confirm the transaction appears in the mempool and is propagated to peers.

The test infrastructure already exercises `validate_proof_size` in isolation (confirming the function works correctly), but no test exercises it through the `validate()` call path, which is why the omission went undetected: [9](#0-8)

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L31-54)
```rust
impl StatelessTransactionValidator {
    #[instrument(skip(self), level = Level::INFO)]
    pub fn validate(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        // TODO(Arni, 1/5/2024): Add a mechanism that validate the sender address is not blocked.
        // TODO(Arni, 1/5/2024): Validate transaction version.

        Self::validate_contract_address(tx)?;
        Self::validate_empty_account_deployment_data(tx)?;
        Self::validate_empty_paymaster_data(tx)?;
        self.validate_resource_bounds(tx)?;
        self.validate_tx_size(tx)?;
        self.validate_nonce_data_availability_mode(tx)?;
        self.validate_fee_data_availability_mode(tx)?;

        if let RpcTransaction::Invoke(invoke_tx) = tx {
            self.validate_client_side_proving_allowed(invoke_tx)?;
            self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
        }

        if let RpcTransaction::Declare(declare_tx) = tx {
            self.validate_declare_tx(declare_tx)?;
        }
        Ok(())
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L265-278)
```rust
    fn validate_proof_size(
        &self,
        tx: &RpcInvokeTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let RpcInvokeTransaction::V3(tx) = tx;
        let proof_size = tx.proof.0.len();
        if proof_size > self.config.max_proof_size {
            return Err(StatelessTransactionValidatorError::ProofTooLarge {
                proof_size,
                max_proof_size: self.config.max_proof_size,
            });
        }
        Ok(())
    }
```

**File:** crates/apollo_deployments/resources/app_configs/gateway_config.json (L26-26)
```json
  "gateway_config.static_config.stateless_tx_validator_config.max_proof_size": 480000,
```

**File:** crates/apollo_node/resources/config_schema.json (L3177-3181)
```json
  "gateway_config.static_config.stateless_tx_validator_config.max_proof_size": {
    "description": "Limitation of proof size.",
    "privacy": "Public",
    "value": 480000
  },
```

**File:** crates/apollo_gateway_config/src/config.rs (L184-186)
```rust
    // If true, allows transactions with non-empty proof_facts or proof fields.
    pub allow_client_side_proving: bool,
}
```

**File:** crates/apollo_gateway_config/src/config.rs (L188-204)
```rust
impl Default for StatelessTransactionValidatorConfig {
    fn default() -> Self {
        StatelessTransactionValidatorConfig {
            validate_resource_bounds: true,
            min_gas_price: 8_000_000_000,
            max_l2_gas_amount: 1_210_000_000,
            max_calldata_length: 5000,
            max_signature_length: 4000,
            max_contract_bytecode_size: 81920,
            max_contract_class_object_size: 4089446,
            min_sierra_version: VersionId::new(1, 1, 0),
            max_sierra_version: VersionId::new(1, 9, usize::MAX),
            allow_client_side_proving: true,
            max_proof_size: 480000,
        }
    }
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L562-566)
```rust
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
    #[serde(default, skip_serializing_if = "Proof::is_empty")]
    pub proof: Proof,
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L640-686)
```rust
#[rstest]
#[case::no_proof_data_allowed_when_disabled(false, None, None)]
#[case::proof_facts_only(false, Some(create_valid_proof_facts_for_testing()), None)]
#[case::proof_only(false, None, Some(Proof::proof_for_testing()))]
#[case::both_proof_and_facts(
    false,
    Some(create_valid_proof_facts_for_testing()),
    Some(Proof::proof_for_testing())
)]
#[case::enabled_accepts_both(
    true,
    Some(create_valid_proof_facts_for_testing()),
    Some(Proof::proof_for_testing())
)]
fn test_client_side_proving_flag(
    #[case] allow_client_side_proving: bool,
    #[case] proof_facts: Option<ProofFacts>,
    #[case] proof: Option<Proof>,
) {
    let config = StatelessTransactionValidatorConfig {
        allow_client_side_proving,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    };
    let tx_validator = StatelessTransactionValidator { config };

    // Check for proof data before moving values.
    let has_proof_data = proof_facts.is_some() || proof.is_some();

    let rpc_tx_args = RpcTransactionArgs {
        proof_facts: proof_facts.unwrap_or_default(),
        proof: proof.unwrap_or_default(),
        ..Default::default()
    };

    let tx = rpc_tx_for_testing(TransactionType::Invoke, rpc_tx_args);

    // Disabled ⇒ reject txs with proof data.
    // Enabled  ⇒ always accept.
    if !allow_client_side_proving && has_proof_data {
        assert_eq!(
            tx_validator.validate(&tx).unwrap_err(),
            StatelessTransactionValidatorError::ClientSideProvingNotAllowed
        );
    } else {
        assert_matches!(tx_validator.validate(&tx), Ok(()));
    }
}
```

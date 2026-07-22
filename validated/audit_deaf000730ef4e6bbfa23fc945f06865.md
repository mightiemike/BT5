### Title
`validate_resource_bounds` Unconditionally Rejects Valid Client-Side Proving Transactions with Zero Fees at Gateway Admission - (`File: crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

The `StatelessTransactionValidator::validate()` function calls `validate_resource_bounds()` before `validate_client_side_proving_allowed()`. The resource-bounds check unconditionally rejects any transaction whose `l2_gas.max_price_per_unit` is below `min_gas_price` (production value: 8 Gwei). Client-side proving transactions are required by the protocol to carry zero `max_price_per_unit` across all resources, so they are always rejected at the gateway before the client-side-proving path is ever reached. The test suite masks this entirely by setting `validate_resource_bounds: false` and `min_gas_price: 0` in `DEFAULT_VALIDATOR_CONFIG_FOR_TESTING`.

### Finding Description

`StatelessTransactionValidator::validate()` executes checks in this order:

```
validate_resource_bounds(tx)?;   // line 40 — runs for ALL tx types
...
if let RpcTransaction::Invoke(invoke_tx) = tx {
    validate_client_side_proving_allowed(invoke_tx)?;  // line 46 — runs only for Invoke
    ...
}
```

Inside `validate_resource_bounds` (lines 56–88), two checks fire unconditionally for every transaction:

1. **Zero-fee check** (line 66):
   ```rust
   if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0) {
       return Err(ZeroResourceBounds { resource_bounds });
   }
   ```
   `max_possible_fee` sums `amount × price` for all three resources. When all `max_price_per_unit` fields are 0, the result is `Fee(0)` and the transaction is rejected.

2. **Minimum gas price check** (line 71):
   ```rust
   if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
       return Err(MaxGasPriceTooLow { ... });
   }
   ```
   Production `min_gas_price` is `8_000_000_000` (8 Gwei).

Client-side proving transactions are required to carry `max_price_per_unit = 0` for all resources. This is enforced by `validate_zero_fee_resource_bounds` in the prover and confirmed by real on-chain data in `block_post_0_14_2.json`:

```json
"resource_bounds": {
    "L1_GAS":      { "max_amount": "0x0",       "max_price_per_unit": "0x0" },
    "L2_GAS":      { "max_amount": "0x5f5e100", "max_price_per_unit": "0x0" },
    "L1_DATA_GAS": { "max_amount": "0x0",       "max_price_per_unit": "0x0" }
}
```

Such a transaction hits check 1 (`max_possible_fee = 0`) and is rejected with `ZeroResourceBounds` before `validate_client_side_proving_allowed` is ever reached. Even if check 1 were bypassed, check 2 (`0 < 8_000_000_000`) would reject it with `MaxGasPriceTooLow`.

The test config (`DEFAULT_VALIDATOR_CONFIG_FOR_TESTING`) sets `validate_resource_bounds: false` and `min_gas_price: 0`, which completely disables both checks and hides the incompatibility.

### Impact Explanation

**High — Mempool/gateway/RPC admission rejects valid transactions before sequencing.**

Any client-side proving Invoke V3 transaction with the protocol-required zero `max_price_per_unit` fields is unconditionally rejected by the gateway's stateless validator with `ZeroResourceBounds` or `MaxGasPriceTooLow`. The transaction never reaches the mempool. This is a denial-of-service against the entire client-side proving feature: every conforming client-side proving transaction is blocked at admission.

### Likelihood Explanation

The production node config (`config_schema.json`) has `allow_client_side_proving: true` and `min_gas_price: 8_000_000_000`. Any user following the prover's documented requirement to set all `max_price_per_unit` to zero will have their transaction rejected. The bug is triggered by every conforming client-side proving transaction submitted to a production gateway.

### Recommendation

In `validate_resource_bounds`, detect whether the incoming Invoke transaction carries non-empty `proof_facts` and skip the `ZeroResourceBounds` and `min_gas_price` checks for that case. Concretely:

```rust
fn validate_resource_bounds(&self, tx: &RpcTransaction) -> ... {
    if !self.config.validate_resource_bounds { return Ok(()); }

    // Client-side proving transactions legitimately carry zero max_price_per_unit.
    let is_client_side_proving = matches!(
        tx,
        RpcTransaction::Invoke(RpcInvokeTransaction::V3(t)) if !t.proof_facts.is_empty()
    );
    if is_client_side_proving { return Ok(()); }

    let resource_bounds = *tx.resource_bounds();
    if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0) {
        return Err(ZeroResourceBounds { resource_bounds });
    }
    if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
        return Err(MaxGasPriceTooLow { ... });
    }
    ...
}
```

Alternatively, reorder the checks so `validate_client_side_proving_allowed` runs first and the resource-bounds check is gated on the transaction not being a client-side proving transaction.

### Proof of Concept

1. Configure a gateway node with production defaults: `allow_client_side_proving: true`, `validate_resource_bounds: true`, `min_gas_price: 8_000_000_000`.
2. Construct a valid client-side proving Invoke V3 transaction with `proof_facts` non-empty and all `max_price_per_unit = 0` (matching the format in `block_post_0_14_2.json` and required by `validate_zero_fee_resource_bounds`).
3. Submit the transaction to the gateway.
4. Observe that `StatelessTransactionValidator::validate_resource_bounds` fires at line 66 and returns `Err(ZeroResourceBounds { ... })` — the transaction is rejected before `validate_client_side_proving_allowed` is reached.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L33-54)
```rust
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-88)
```rust
    fn validate_resource_bounds(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if !self.config.validate_resource_bounds {
            return Ok(());
        }

        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }

        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
        }

        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }

        Ok(())
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L54-67)
```rust
static DEFAULT_VALIDATOR_CONFIG_FOR_TESTING: LazyLock<StatelessTransactionValidatorConfig> =
    LazyLock::new(|| StatelessTransactionValidatorConfig {
        validate_resource_bounds: false,
        min_gas_price: 0,
        max_l2_gas_amount: 1_000_000_000,
        max_calldata_length: 10,
        max_signature_length: 1,
        max_proof_size: 10,
        max_contract_bytecode_size: 100_000,
        max_contract_class_object_size: 100_000,
        min_sierra_version: *MIN_SIERRA_VERSION,
        max_sierra_version: *MAX_SIERRA_VERSION,
        allow_client_side_proving: true,
    });
```

**File:** crates/apollo_node/resources/config_schema.json (L3152-3206)
```json
  "gateway_config.static_config.stateless_tx_validator_config.allow_client_side_proving": {
    "description": "If true, allows transactions with non-empty proof_facts or proof fields.",
    "privacy": "Public",
    "value": true
  },
  "gateway_config.static_config.stateless_tx_validator_config.max_calldata_length": {
    "description": "Limitation of calldata length.",
    "privacy": "Public",
    "value": 5000
  },
  "gateway_config.static_config.stateless_tx_validator_config.max_contract_bytecode_size": {
    "description": "Limitation of contract class bytecode size.",
    "privacy": "Public",
    "value": 81920
  },
  "gateway_config.static_config.stateless_tx_validator_config.max_contract_class_object_size": {
    "description": "Limitation of contract class object size.",
    "privacy": "Public",
    "value": 4089446
  },
  "gateway_config.static_config.stateless_tx_validator_config.max_l2_gas_amount": {
    "description": "Maximum allowed L2 gas amount for transactions.",
    "privacy": "Public",
    "value": 1210000000
  },
  "gateway_config.static_config.stateless_tx_validator_config.max_proof_size": {
    "description": "Limitation of proof size.",
    "privacy": "Public",
    "value": 480000
  },
  "gateway_config.static_config.stateless_tx_validator_config.max_sierra_version.major": {
    "description": "The major version of the configuration.",
    "privacy": "Public",
    "value": 1
  },
  "gateway_config.static_config.stateless_tx_validator_config.max_sierra_version.minor": {
    "description": "The minor version of the configuration.",
    "privacy": "Public",
    "value": 9
  },
  "gateway_config.static_config.stateless_tx_validator_config.max_sierra_version.patch": {
    "description": "The patch version of the configuration.",
    "privacy": "Public",
    "value": 18446744073709551615
  },
  "gateway_config.static_config.stateless_tx_validator_config.max_signature_length": {
    "description": "Limitation of signature length.",
    "privacy": "Public",
    "value": 4000
  },
  "gateway_config.static_config.stateless_tx_validator_config.min_gas_price": {
    "description": "Minimum gas price for transactions.",
    "privacy": "Public",
    "value": 8000000000
  },
```

**File:** crates/apollo_starknet_client/resources/reader/block_post_0_14_2.json (L330-367)
```json
            "resource_bounds": {
                "L1_GAS": {
                    "max_amount": "0x0",
                    "max_price_per_unit": "0x0"
                },
                "L2_GAS": {
                    "max_amount": "0x5f5e100",
                    "max_price_per_unit": "0x0"
                },
                "L1_DATA_GAS": {
                    "max_amount": "0x0",
                    "max_price_per_unit": "0x0"
                }
            },
            "tip": "0x0",
            "paymaster_data": [],
            "sender_address": "0x2a6ad2b2cd76eba6cf3a68568f4c01106dd9237b9cd79c7d8895bcdf64b490",
            "calldata": [
                "0x1",
                "0x70a5da4f557b77a9c54546e4bcc900806e28793d8e3eaaa207428d2387249b7",
                "0x83afd3f4caedc6eebf44246fe54e38c95e3179a5ec9ea81740eca5b482d12e",
                "0x3",
                "0x6e1800c44c5caff653fadf32c75381696b9e7d660fcac68a8ff8e1238c7c05b",
                "0x0",
                "0x0"
            ],
            "account_deployment_data": [],
            "proof_facts": [
                "0x50524f4f4630",
                "0x5649525455414c5f534e4f53",
                "0x9743416d2d92b680d47338cb89f3def2e77ba772bbc2e568aeb48425e6c450",
                "0x5649525455414c5f534e4f5330",
                "0xf361e",
                "0x59fd5060b341eef5d3225eb5c9c7c7cbb468ac509317e116505bdbe7edd08e",
                "0x6989a681c469d769f3a706c56550a63741a4b2d32bef4b1209a26daad1dbb6",
                "0x0"
            ],
            "type": "INVOKE_FUNCTION"
```

**File:** crates/starknet_transaction_prover/src/proving/virtual_snos_prover.rs (L401-445)
```rust
fn validate_zero_fee_resource_bounds(
    tx: &RpcInvokeTransactionV3,
) -> Result<(), VirtualSnosProverError> {
    let bounds = &tx.resource_bounds;
    let mut violations = Vec::new();

    if bounds.l1_gas.max_price_per_unit != GasPrice(0) {
        violations
            .push(format!("l1_gas.max_price_per_unit = {}", bounds.l1_gas.max_price_per_unit.0));
    }
    if bounds.l2_gas.max_price_per_unit != GasPrice(0) {
        violations
            .push(format!("l2_gas.max_price_per_unit = {}", bounds.l2_gas.max_price_per_unit.0));
    }
    if bounds.l1_data_gas.max_price_per_unit != GasPrice(0) {
        violations.push(format!(
            "l1_data_gas.max_price_per_unit = {}",
            bounds.l1_data_gas.max_price_per_unit.0
        ));
    }
    if tx.tip != Tip(0) {
        violations.push(format!("tip = {}", tx.tip.0));
    }

    if !violations.is_empty() {
        return Err(VirtualSnosProverError::InvalidTransactionInput(format!(
            "Proving is client-side — no fees are charged. The following fields must be zero but \
             were not: [{}]. Set all max_price_per_unit fields and tip to 0x0. Note: max_amount \
             fields are fine to set — l2_gas.max_amount controls the gas limit enforced by the OS \
             (use the value from starknet_estimateFee, or 100000000 as a safe upper bound). \
             l1_gas.max_amount and l1_data_gas.max_amount do not affect OS execution.",
            violations.join(", ")
        )));
    }

    if bounds.l2_gas.max_amount == GasAmount(0) {
        return Err(VirtualSnosProverError::InvalidTransactionInput(
            "l2_gas.max_amount must be non-zero — it is the gas limit enforced by the OS on the \
             transaction. Set this to the value returned by starknet_estimateFee, or use \
             100000000 (0x5f5e100) as a safe upper bound (sufficient for ~1 million Cairo steps)."
                .to_string(),
        ));
    }

    Ok(())
```

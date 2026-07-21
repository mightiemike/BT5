### Title
Missing L1 and L1-data gas price minimum validation in gateway admission allows invalid transactions to enter the mempool — (File: `crates/apollo_gateway/src/stateless_transaction_validator.rs`, `crates/apollo_gateway/src/stateful_transaction_validator.rs`)

---

### Summary

The gateway's resource-bounds admission logic enforces a minimum price floor only on the **L2 gas** dimension of `AllResourceBounds` transactions. Neither the stateless nor the stateful validator checks `l1_gas.max_price_per_unit` or `l1_data_gas.max_price_per_unit` against any minimum. The blockifier's `check_fee_bounds` enforces all three dimensions at execution time. The gap means a transaction whose L1 or L1-data gas price is set below the actual block price passes every gateway check, enters the mempool, and is then deterministically rejected by the blockifier — the sequencer admits transactions it will never be able to execute.

---

### Finding Description

**Stateless validator** (`validate_resource_bounds`):

```rust
// crates/apollo_gateway/src/stateless_transaction_validator.rs  lines 71-76
if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
    return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow { … });
}
```

Only `l2_gas.max_price_per_unit` is compared against the static `min_gas_price` (default `8_000_000_000`). `l1_gas.max_price_per_unit` and `l1_data_gas.max_price_per_unit` are never checked. [1](#0-0) 

**Stateful validator** (`validate_tx_l2_gas_price_within_threshold`):

```rust
// crates/apollo_gateway/src/stateful_transaction_validator.rs  lines 358-390
// TODO(Arni): Consider running this validation for all gas prices.
fn validate_tx_l2_gas_price_within_threshold(…) {
    match tx_resource_bounds {
        ValidResourceBounds::AllResources(tx_resource_bounds) => {
            let tx_l2_gas_price = tx_resource_bounds.l2_gas.max_price_per_unit;
            // … only l2_gas checked …
        }
        ValidResourceBounds::L1Gas(_) => {
            // No validation required for legacy transactions.
        }
    }
}
```

The function name and the embedded TODO both acknowledge that L1 and L1-data gas prices are not validated. The dynamic threshold (percentage of the previous block's L2 gas price) is applied only to `l2_gas.max_price_per_unit`. [2](#0-1) 

**Blockifier pre-validation** (`check_fee_bounds`) — the enforcement that the gateway skips:

For `AllResources` transactions the blockifier checks all three resources:

```rust
// crates/blockifier/src/transaction/account_transaction.rs  lines 398-424
ValidResourceBounds::AllResources(AllResourceBounds {
    l1_gas: l1_gas_resource_bounds,
    l2_gas: l2_gas_resource_bounds,
    l1_data_gas: l1_data_gas_resource_bounds,
}) => {
    let GasPriceVector { l1_gas_price, l1_data_gas_price, l2_gas_price } =
        block_info.gas_prices.gas_price_vector(fee_type);
    vec![
        (L1Gas,     l1_gas_resource_bounds,     …, *l1_gas_price),
        (L1DataGas, l1_data_gas_resource_bounds, …, *l1_data_gas_price),
        (L2Gas,     l2_gas_resource_bounds,      …, *l2_gas_price),
    ]
}
// … if resource_bounds.max_price_per_unit < actual_gas_price.get() → error
``` [3](#0-2) 

**Config** — `StatelessTransactionValidatorConfig` has `min_gas_price` only for L2 gas; there is no `min_l1_gas_price` or `min_l1_data_gas_price` field. [4](#0-3) 

---

### Impact Explanation

An unprivileged caller can craft an `AllResourceBounds` V3 transaction with:

- `l2_gas.max_price_per_unit` ≥ `min_gas_price` (passes stateless check)
- `l2_gas.max_price_per_unit` ≥ dynamic threshold (passes stateful check)
- `l1_gas.max_price_per_unit = 0` and/or `l1_data_gas.max_price_per_unit = 0`

The transaction passes both gateway validators, is assigned a valid `tx_hash` via `calculate_transaction_hash`, and is inserted into the mempool. When the batcher pulls it for execution, `check_fee_bounds` immediately rejects it with `InsufficientResourceBounds { MaxGasPriceTooLow { resource: L1Gas … } }`. The transaction is sequenced but never successfully executed.

This matches the **High** impact scope: *"Mempool/gateway/RPC admission accepts invalid transactions … before sequencing."*

---

### Likelihood Explanation

The trigger requires only a standard V3 `starknet_addInvokeTransaction` RPC call with `l1_gas.max_price_per_unit = 0`. No privileged access, no special account, no malformed bytes — the transaction is structurally valid and passes all other gateway checks. The L1 gas price on mainnet is always non-zero, so every such transaction deterministically fails execution. The condition is trivially reproducible.

---

### Recommendation

1. **Stateless validator**: Add parallel checks for `l1_gas.max_price_per_unit` and `l1_data_gas.max_price_per_unit` against a configured minimum (or against `min_gas_price`), mirroring the existing L2 check at `crates/apollo_gateway/src/stateless_transaction_validator.rs` line 71.

2. **Stateful validator**: Resolve the `TODO(Arni)` at `crates/apollo_gateway/src/stateful_transaction_validator.rs` line 358 by extending `validate_tx_l2_gas_price_within_threshold` to also compare `l1_gas.max_price_per_unit` and `l1_data_gas.max_price_per_unit` against the previous block's corresponding prices (multiplied by `min_gas_price_percentage / 100`).

3. **Config**: Add `min_l1_gas_price` and `min_l1_data_gas_price` fields to `StatelessTransactionValidatorConfig` (or reuse `min_gas_price` for all three dimensions) so operators can tune the floor independently.

---

### Proof of Concept

```
POST /gateway/add_transaction
{
  "type": "INVOKE",
  "version": "0x3",
  "sender_address": "<valid_account>",
  "calldata": [...],
  "nonce": "0x0",
  "signature": [...],
  "resource_bounds": {
    "l1_gas":      { "max_amount": "0x1",   "max_price_per_unit": "0x0" },   // ← zero price, no gateway check
    "l2_gas":      { "max_amount": "0x100", "max_price_per_unit": "0x1DCD6500" }, // ≥ min_gas_price
    "l1_data_gas": { "max_amount": "0x1",   "max_price_per_unit": "0x0" }    // ← zero price, no gateway check
  },
  "tip": "0x0",
  "paymaster_data": [],
  "account_deployment_data": [],
  "nonce_data_availability_mode": "L1",
  "fee_data_availability_mode": "L1"
}
```

**Expected (current behaviour):** Gateway returns `{ "code": "TRANSACTION_RECEIVED" }`. Transaction enters the mempool. Batcher pulls it; blockifier `check_fee_bounds` fires `InsufficientResourceBounds { MaxGasPriceTooLow { resource: L1Gas } }`. Transaction is never executed.

**Expected (after fix):** Gateway returns `GAS_PRICE_TOO_LOW` at admission, transaction is rejected before entering the mempool.

### Citations

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

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L358-390)
```rust
    // TODO(Arni): Consider running this validation for all gas prices.
    fn validate_tx_l2_gas_price_within_threshold(
        &self,
        tx_resource_bounds: ValidResourceBounds,
        previous_block_l2_gas_price: NonzeroGasPrice,
    ) -> StatefulTransactionValidatorResult<()> {
        match tx_resource_bounds {
            ValidResourceBounds::AllResources(tx_resource_bounds) => {
                let tx_l2_gas_price = tx_resource_bounds.l2_gas.max_price_per_unit;
                let gas_price_threshold_multiplier =
                    Ratio::new(self.config.min_gas_price_percentage.into(), 100_u128);
                let threshold = (gas_price_threshold_multiplier
                    * previous_block_l2_gas_price.get().0)
                    .to_integer();
                if tx_l2_gas_price.0 < threshold {
                    return Err(StarknetError {
                        // We didn't have this kind of an error.
                        code: StarknetErrorCode::UnknownErrorCode(
                            "StarknetErrorCode.GAS_PRICE_TOO_LOW".to_string(),
                        ),
                        message: format!(
                            "Transaction L2 gas price {tx_l2_gas_price} is below the required \
                             threshold {threshold}.",
                        ),
                    });
                }
            }
            ValidResourceBounds::L1Gas(_) => {
                // No validation required for legacy transactions.
            }
        }
        Ok(())
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L398-458)
```rust
                    ValidResourceBounds::AllResources(AllResourceBounds {
                        l1_gas: l1_gas_resource_bounds,
                        l2_gas: l2_gas_resource_bounds,
                        l1_data_gas: l1_data_gas_resource_bounds,
                    }) => {
                        let GasPriceVector { l1_gas_price, l1_data_gas_price, l2_gas_price } =
                            block_info.gas_prices.gas_price_vector(fee_type);
                        vec![
                            (
                                L1Gas,
                                l1_gas_resource_bounds,
                                minimal_gas_amount_vector.l1_gas,
                                *l1_gas_price,
                            ),
                            (
                                L1DataGas,
                                l1_data_gas_resource_bounds,
                                minimal_gas_amount_vector.l1_data_gas,
                                *l1_data_gas_price,
                            ),
                            (
                                L2Gas,
                                l2_gas_resource_bounds,
                                minimal_gas_amount_vector.l2_gas,
                                *l2_gas_price,
                            ),
                        ]
                    }
                };
                let insufficiencies = resources_amount_tuple
                    .iter()
                    .flat_map(
                        |(resource, resource_bounds, minimal_gas_amount, actual_gas_price)| {
                            let mut insufficiencies_resource = vec![];
                            if minimal_gas_amount > &resource_bounds.max_amount {
                                insufficiencies_resource.push(
                                    ResourceBoundsError::MaxGasAmountTooLow {
                                        resource: *resource,
                                        max_gas_amount: resource_bounds.max_amount,
                                        minimal_gas_amount: *minimal_gas_amount,
                                    },
                                );
                            }
                            if resource_bounds.max_price_per_unit < actual_gas_price.get() {
                                insufficiencies_resource.push(
                                    ResourceBoundsError::MaxGasPriceTooLow {
                                        resource: *resource,
                                        max_gas_price: resource_bounds.max_price_per_unit,
                                        actual_gas_price: (*actual_gas_price).into(),
                                    },
                                );
                            }
                            insufficiencies_resource
                        },
                    )
                    .collect::<Vec<_>>();
                if !insufficiencies.is_empty() {
                    return Err(Box::new(TransactionFeeError::InsufficientResourceBounds {
                        errors: insufficiencies,
                    }))?;
                }
```

**File:** crates/apollo_gateway_config/src/config.rs (L166-204)
```rust
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, Validate)]
pub struct StatelessTransactionValidatorConfig {
    // If true, ensures that at least one resource bound (L1, L2, or L1 data) is greater than zero.
    pub validate_resource_bounds: bool,
    // TODO(AlonH): Remove the `min_gas_price` field from this struct and use the one from the
    // versioned constants.
    pub min_gas_price: u128,
    pub max_l2_gas_amount: u64,
    pub max_calldata_length: usize,
    pub max_signature_length: usize,
    pub max_proof_size: usize,

    // Declare txs specific config.
    pub max_contract_bytecode_size: usize,
    pub max_contract_class_object_size: usize,
    pub min_sierra_version: VersionId,
    pub max_sierra_version: VersionId,

    // If true, allows transactions with non-empty proof_facts or proof fields.
    pub allow_client_side_proving: bool,
}

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

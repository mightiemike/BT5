### Title
Gateway `validate_resource_bounds` Validates Only `l2_gas` Price, Leaving `l1_gas` and `l1_data_gas` Prices Unguarded — (`crates/apollo_gateway/src/stateless_transaction_validator.rs`, `crates/apollo_gateway/src/stateful_transaction_validator.rs`)

---

### Summary

The gateway's resource-bounds validation enforces a minimum price floor only on `l2_gas.max_price_per_unit`. The analogous floor for `l1_gas.max_price_per_unit` and `l1_data_gas.max_price_per_unit` is absent in both the stateless and stateful validators. A transaction that sets those two prices to zero (or any value below the current block's L1 gas price) passes every gateway check and is admitted to the mempool, yet it will always be rejected by the blockifier's `check_fee_bounds` during sequencing. This is the direct sequencer analog of the external report: just as `rescueTokens` protected `lpToken`/`muteToken` but left `fee0`/`fee1` unguarded, the gateway protects `l2_gas` price but leaves `l1_gas` and `l1_data_gas` prices unguarded.

---

### Finding Description

**Stateless validator — only `l2_gas` price is checked**

`StatelessTransactionValidator::validate_resource_bounds` in `crates/apollo_gateway/src/stateless_transaction_validator.rs` performs two checks when `validate_resource_bounds = true`:

1. The aggregate fee across all three resources is non-zero.
2. `l2_gas.max_price_per_unit >= config.min_gas_price`. [1](#0-0) 

`StatelessTransactionValidatorConfig` has a single `min_gas_price: u128` field (default `8_000_000_000`) that is applied exclusively to `l2_gas`. There is no `min_l1_gas_price` or `min_l1_data_gas_price` field. [2](#0-1) 

**Stateful validator — same gap, acknowledged by a TODO**

`StatefulTransactionValidator::validate_tx_l2_gas_price_within_threshold` checks only `l2_gas.max_price_per_unit` against a percentage of the previous block's L2 gas price. The function carries an explicit TODO:

```
// TODO(Arni): Consider running this validation for all gas prices.
``` [3](#0-2) 

The stateful `validate_resource_bounds` wrapper calls only `validate_tx_l2_gas_price_within_threshold`; no equivalent call exists for L1 or L1-data gas prices. [4](#0-3) 

**Blockifier enforces all three prices — but only at execution time**

`AccountTransaction::check_fee_bounds` in the blockifier checks all three gas prices against the actual block prices for `AllResources` transactions: [5](#0-4) 

A transaction with `l1_gas.max_price_per_unit = 0` will reach this check and fail with `MaxGasPriceTooLow { resource: L1Gas }` — but only after it has already been admitted to the mempool and handed to the batcher.

---

### Impact Explanation

Any unprivileged user can craft a V3 (`AllResources`) transaction with:
- `l2_gas.max_price_per_unit >= min_gas_price` (passes stateless check)
- `l1_gas.max_price_per_unit = 0` and `l1_data_gas.max_price_per_unit = 0` (not checked)

Such a transaction passes both the stateless and stateful gateway validators, is admitted to the mempool, and is eventually fetched by the batcher. The blockifier then rejects it at `check_fee_bounds`, wasting sequencer resources. At scale this constitutes a mempool-flooding / DoS vector: the gateway's admission control, which is the intended first line of defense, fails to reject these transactions.

**Impact**: High — Mempool/gateway admission accepts invalid transactions before sequencing.

---

### Likelihood Explanation

The attack requires no privilege, no special account state, and no on-chain funds. Any caller of `starknet_addInvokeTransaction` can set `l1_gas.max_price_per_unit = 0` in the `resource_bounds` field. The gateway config ships with `validate_resource_bounds: true` by default, so the incomplete check is active in production. The TODO comment in the stateful validator confirms the gap is known but unresolved.

---

### Recommendation

1. **Stateless validator**: Add minimum-price checks for `l1_gas` and `l1_data_gas` analogous to the existing `l2_gas` check. Either reuse `min_gas_price` or introduce separate `min_l1_gas_price` / `min_l1_data_gas_price` config fields.

```rust
// In validate_resource_bounds:
if resource_bounds.l1_gas.max_price_per_unit.0 < self.config.min_l1_gas_price {
    return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow { ... });
}
if resource_bounds.l1_data_gas.max_price_per_unit.0 < self.config.min_l1_data_gas_price {
    return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow { ... });
}
```

2. **Stateful validator**: Resolve the TODO by extending `validate_tx_l2_gas_price_within_threshold` (or a renamed successor) to also compare `l1_gas.max_price_per_unit` and `l1_data_gas.max_price_per_unit` against the corresponding previous-block prices, using the same percentage-threshold logic already applied to L2 gas.

---

### Proof of Concept

1. Construct a V3 invoke transaction with `AllResourceBounds`:
   - `l2_gas = { max_amount: 1, max_price_per_unit: 8_000_000_000 }` (meets `min_gas_price`)
   - `l1_gas = { max_amount: 1, max_price_per_unit: 0 }` (zero — not checked)
   - `l1_data_gas = { max_amount: 1, max_price_per_unit: 0 }` (zero — not checked)

2. Submit via `starknet_addInvokeTransaction`.

3. `StatelessTransactionValidator::validate_resource_bounds` passes: aggregate fee is non-zero (from `l2_gas`), and `l2_gas.max_price_per_unit >= min_gas_price`. [6](#0-5) 

4. `StatefulTransactionValidator::validate_resource_bounds` passes: only `l2_gas` price is compared against the previous block threshold. [7](#0-6) 

5. Transaction enters the mempool. When the batcher executes it, `check_fee_bounds` compares `l1_gas.max_price_per_unit = 0` against the actual block L1 gas price (e.g., `1_000_000_000`) and returns `InsufficientResourceBounds { MaxGasPriceTooLow { resource: L1Gas } }`. [8](#0-7) 

6. The transaction is discarded after consuming sequencer resources. Repeat at scale for DoS.

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

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L223-243)
```rust
    async fn validate_resource_bounds(
        &self,
        executable_tx: &ExecutableTransaction,
    ) -> StatefulTransactionValidatorResult<()> {
        // Skip this validation during the systems bootstrap phase.
        if self.config.validate_resource_bounds {
            // TODO(Arni): getnext_l2_gas_price from the block header.
            let previous_block_l2_gas_price = self
                .gateway_fixed_block_state_reader
                .get_block_info()
                .await?
                .gas_prices
                .strk_gas_prices
                .l2_gas_price;
            self.validate_tx_l2_gas_price_within_threshold(
                executable_tx.resource_bounds(),
                previous_block_l2_gas_price,
            )?;
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

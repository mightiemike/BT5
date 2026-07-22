### Title
Gateway `validate_resource_bounds` Omits L1/L1_data Gas Price Threshold Checks, Accepting Transactions the Blockifier Rejects During Pre-Validation — (File: crates/apollo_gateway/src/stateful_transaction_validator.rs)

---

### Summary

The gateway's stateful validator only checks the L2 gas price against the previous block's price. It explicitly skips any price check for `L1Gas`-typed transactions and never checks `l1_gas.max_price_per_unit` or `l1_data_gas.max_price_per_unit` for `AllResources` transactions. The blockifier enforces all three gas-price lower bounds during pre-validation. The result is a direct analog to the `maxWithdraw`/`maxRedeem` bug: the gateway's admission path reports a transaction as accepted (returns a transaction hash) without accounting for the same cap the execution layer enforces, so the reported acceptance is wrong and the transaction is silently dropped.

---

### Finding Description

`validate_tx_l2_gas_price_within_threshold` in `StatefulTransactionValidator` matches on `ValidResourceBounds`:

```rust
// crates/apollo_gateway/src/stateful_transaction_validator.rs
fn validate_tx_l2_gas_price_within_threshold(
    &self,
    tx_resource_bounds: ValidResourceBounds,
    previous_block_l2_gas_price: NonzeroGasPrice,
) -> StatefulTransactionValidatorResult<()> {
    match tx_resource_bounds {
        ValidResourceBounds::AllResources(tx_resource_bounds) => {
            let tx_l2_gas_price = tx_resource_bounds.l2_gas.max_price_per_unit;
            // ... only l2_gas price is checked
        }
        ValidResourceBounds::L1Gas(_) => {
            // No validation required for legacy transactions.
        }
    }
    Ok(())
}
``` [1](#0-0) 

For `AllResources` transactions only `l2_gas.max_price_per_unit` is compared against the threshold; `l1_gas.max_price_per_unit` and `l1_data_gas.max_price_per_unit` are never read. For `L1Gas` transactions the arm is a no-op.

The blockifier's pre-validation in `account_transaction.rs` enforces all three prices:

```rust
// crates/blockifier/src/transaction/account_transaction.rs
ValidResourceBounds::AllResources(AllResourceBounds {
    l1_gas: l1_gas_resource_bounds,
    l2_gas: l2_gas_resource_bounds,
    l1_data_gas: l1_data_gas_resource_bounds,
}) => {
    vec![
        (L1Gas,     l1_gas_resource_bounds,     ..., *l1_gas_price),
        (L1DataGas, l1_data_gas_resource_bounds, ..., *l1_data_gas_price),
        (L2Gas,     l2_gas_resource_bounds,      ..., *l2_gas_price),
    ]
}
``` [2](#0-1) 

For each resource the blockifier checks:

```rust
if resource_bounds.max_price_per_unit < actual_gas_price.get() {
    insufficiencies_resource.push(ResourceBoundsError::MaxGasPriceTooLow { ... });
}
``` [3](#0-2) 

A transaction whose `l1_gas.max_price_per_unit` is below the current block's L1 gas price therefore passes every gateway check (stateless `validate_resource_bounds` only tests L2 price and L2 amount; stateful `validate_tx_l2_gas_price_within_threshold` only tests L2 price) but is rejected by the blockifier with `TransactionFeeError::InsufficientResourceBounds` during pre-validation — before any fee is charged or nonce consumed.

The stateless validator has the same gap and an explicit TODO acknowledging it:

```rust
// crates/apollo_gateway/src/stateless_transaction_validator.rs
// TODO(Arni): Consider running this validation for all gas prices.
``` [4](#0-3) 

The gateway's `run_validate_entry_point` uses `BouncerConfig::max()` and runs only the account's `__validate__` entry point; it does not re-check gas prices, so no downstream gate catches the discrepancy before the transaction reaches the batcher. [5](#0-4) 

---

### Impact Explanation

**High — Mempool/gateway admission accepts transactions that will be rejected before sequencing.**

The gateway

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L325-330)
```rust
        let block_context = BlockContext::new(
            block_info,
            self.chain_info.clone(),
            versioned_constants,
            BouncerConfig::max(),
        );
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L359-390)
```rust
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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L398-425)
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
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L441-450)
```rust
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

### Title
Gateway Stateless Validator Skips `max_l2_gas_amount` Admission Check for Declare Transactions — (`File: crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

The `validate_resource_bounds` function in the stateless gateway validator enforces a `max_l2_gas_amount` cap on Invoke and DeployAccount transactions but explicitly skips this check for Declare transactions. An unprivileged user can submit a Declare transaction with `l2_gas.max_amount` set to an arbitrarily large value (e.g., `u64::MAX`), bypassing the gateway's own admission-control invariant and causing the transaction to be accepted into the mempool.

### Finding Description

In `StatelessTransactionValidator::validate_resource_bounds`, after checking that the resource bounds are non-zero and that the L2 gas price meets the minimum, the code enforces an upper bound on `l2_gas.max_amount`:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

The `if let RpcTransaction::Declare(_) = tx { }` branch is an empty no-op that causes the entire `max_l2_gas_amount` check to be skipped for Declare transactions. The production configuration sets `max_l2_gas_amount = 1_210_000_000`. A Declare transaction with `l2_gas.max_amount = u64::MAX` passes this function without error.

The stateful validator's `validate_tx_l2_gas_price_within_threshold` only checks the *price* per unit, not the *amount*, and also only for `AllResources` bounds:

```rust
ValidResourceBounds::L1Gas(_) => {
    // No validation required for legacy transactions.
}
```

Neither validator path enforces the amount cap for Declare transactions. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation

A Declare transaction with `l2_gas.max_amount` far exceeding the block's maximum gas capacity passes stateless validation, passes stateful validation (which only checks price), and is admitted to the mempool. This violates the gateway's own admission-control invariant: the `max_l2_gas_amount` limit is intended to prevent any single transaction from claiming more L2 gas than the block can accommodate. Because the check is absent for Declare, the gateway accepts transactions it is explicitly configured to reject, matching the impact: **"Mempool/gateway/RPC admission accepts invalid transactions before sequencing."** [4](#0-3) 

### Likelihood Explanation

The trigger requires only a standard Declare transaction with a crafted `l2_gas.max_amount` field. No privileged access, special keys, or malformed bytes are needed. Any user who can submit a transaction to the gateway can exercise this path. The TODO comment confirms the gap is known but unaddressed. [1](#0-0) 

### Recommendation

Remove the empty `if let RpcTransaction::Declare(_) = tx { }` branch and apply the `max_l2_gas_amount` check unconditionally to all transaction types:

```rust
if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

If Declare transactions legitimately require a higher L2 gas ceiling (e.g., due to compilation costs), introduce a separate `max_l2_gas_amount_declare` config parameter rather than removing the check entirely. [4](#0-3) 

### Proof of Concept

1. Construct an `RpcDeclareTransactionV3` with `resource_bounds.l2_gas.max_amount = GasAmount(u64::MAX)` and all other fields valid.
2. Submit it to the gateway's `starknet_addDeclareTransaction` endpoint.
3. Observe that `StatelessTransactionValidator::validate_resource_bounds` returns `Ok(())` — the `MaxGasAmountTooHigh` error is never raised for Declare.
4. The transaction proceeds through stateful validation (which only checks L2 gas *price*) and is inserted into the mempool with a declared L2 gas amount orders of magnitude above the configured block maximum.

The test `valid_l2_gas_amount_on_declare` in `stateless_transaction_validator_test.rs` explicitly confirms this behavior is currently accepted:

```rust
fn valid_l2_gas_amount_on_declare(
    #[case] config: StatelessTransactionValidatorConfig,
    #[case] rpc_tx_args: RpcTransactionArgs,
) {
    let tx_type = TransactionType::Declare;
    ...
    assert_matches!(tx_validator.validate(&tx), Ok(()));
}
``` [5](#0-4) [1](#0-0)

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

**File:** crates/apollo_node/resources/config_schema.json (L3172-3176)
```json
  "gateway_config.static_config.stateless_tx_validator_config.max_l2_gas_amount": {
    "description": "Maximum allowed L2 gas amount for transactions.",
    "privacy": "Public",
    "value": 1210000000
  },
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L173-201)
```rust
#[rstest]
#[case::l2_gas_amount_out_of_limit(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        max_l2_gas_amount: 100,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l2_gas: ResourceBounds {
                max_amount: GasAmount(200),
                ..NON_EMPTY_RESOURCE_BOUNDS
            },
            ..Default::default()
        },
        ..Default::default()
    }
)]
fn valid_l2_gas_amount_on_declare(
    #[case] config: StatelessTransactionValidatorConfig,
    #[case] rpc_tx_args: RpcTransactionArgs,
) {
    let tx_type = TransactionType::Declare;
    let tx_validator = StatelessTransactionValidator { config };

    let tx = rpc_tx_for_testing(tx_type, rpc_tx_args);

    assert_matches!(tx_validator.validate(&tx), Ok(()));
}
```

### Title
Gateway Stateless Validator Skips `max_l2_gas_amount` Check for Declare Transactions — (`File: crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

`StatelessTransactionValidator::validate_resource_bounds` enforces an upper bound on `l2_gas.max_amount` for `Invoke` and `DeployAccount` transactions but explicitly skips that check for `Declare` transactions. A `Declare` transaction with an arbitrarily large `l2_gas.max_amount` therefore bypasses the gateway's stateless admission control and is forwarded to the mempool.

### Finding Description

`validate_resource_bounds` applies three checks to every incoming `RpcTransaction`:

1. At least one resource bound must be non-zero.
2. `l2_gas.max_price_per_unit` must be ≥ `config.min_gas_price`.
3. `l2_gas.max_amount` must be ≤ `config.max_l2_gas_amount`.

Check 3 is guarded by an explicit type-dispatch that returns early for `Declare`:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { … });
}
``` [1](#0-0) 

The test suite confirms this is intentional and not a dead-code path: `valid_l2_gas_amount_on_declare` explicitly asserts that a `Declare` transaction with `max_amount: GasAmount(200)` passes when `max_l2_gas_amount: 100` is configured. [2](#0-1) 

The `test_invalid_max_l2_gas_amount` test only parameterises over `TransactionType::DeployAccount` and `TransactionType::Invoke`, leaving `Declare` untested. [3](#0-2) 

The default production value of `max_l2_gas_amount` is `1_210_000_000`. [4](#0-3) 

### Impact Explanation

`max_l2_gas_amount` is the gateway's per-transaction L2-gas ceiling. Its purpose is to prevent any single transaction from claiming a gas budget that exceeds what the sequencer is willing to schedule. Bypassing it for `Declare` transactions means:

- A `Declare` transaction with `l2_gas.max_amount = u64::MAX` passes stateless validation and enters the mempool.
- `verify_can_pay_committed_bounds` in the blockifier computes `max_possible_fee = max_l2_gas_amount × max_price_per_unit`. With `max_l2_gas_amount = u64::MAX` and `max_price_per_unit = min_gas_price`, this product overflows or produces an astronomically large value, causing unpredictable behaviour at the stateful-validation or block-building stage.
- Even at moderate over-limit values (e.g., `max_l2_gas_amount = config.max_l2_gas_amount + 1`), the gateway's admission policy is violated: transactions the operator explicitly configured as inadmissible are accepted.

This matches the **High** impact: *Mempool/gateway/RPC admission accepts invalid transactions before sequencing.* [5](#0-4) 

### Likelihood Explanation

The trigger requires no privilege. Any external caller can craft a `RpcDeclareTransaction::V3` with `resource_bounds.l2_gas.max_amount` set above `config.max_l2_gas_amount` and submit it to the gateway's HTTP endpoint. The stateless validator is the first gate; it runs synchronously before any state read. The bypass is unconditional for all `Declare` variants.

### Recommendation

Remove the `Declare`-specific early-return and apply the same `max_l2_gas_amount` ceiling uniformly:

```rust
if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

If `Declare` transactions genuinely require a higher ceiling (e.g., because compilation is more gas-intensive), introduce a separate `max_l2_gas_amount_declare` config field rather than removing the check entirely.

### Proof of Concept

The existing test `valid_l2_gas_amount_on_declare` already demonstrates the bypass:

```rust
// crates/apollo_gateway/src/stateless_transaction_validator_test.rs
#[case::l2_gas_amount_out_of_limit(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        max_l2_gas_amount: 100,          // operator ceiling = 100
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l2_gas: ResourceBounds {
                max_amount: GasAmount(200), // 200 > 100 — should be rejected
                ..NON_EMPTY_RESOURCE_BOUNDS
            },
            ..Default::default()
        },
        ..Default::default()
    }
)]
fn valid_l2_gas_amount_on_declare(…) {
    let tx_type = TransactionType::Declare;
    // …
    assert_matches!(tx_validator.validate(&tx), Ok(())); // passes — bypass confirmed
}
``` [2](#0-1) 

A new test that should fail (but currently passes) to demonstrate the admission impact:

```rust
fn test_declare_bypasses_max_l2_gas_amount() {
    let config = StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        max_l2_gas_amount: DEFAULT_VALIDATOR_CONFIG.max_l2_gas_amount,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    };
    let tx_args = RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l2_gas: ResourceBounds {
                max_amount: GasAmount(u64::MAX),
                max_price_per_unit: GasPrice(DEFAULT_VALIDATOR_CONFIG.min_gas_price),
            },
            ..Default::default()
        },
        ..Default::default()
    };
    let validator = StatelessTransactionValidator { config };
    let tx = rpc_tx_for_testing(TransactionType::Declare, tx_args);
    // Expected: Err(MaxGasAmountTooHigh { … })
    // Actual:   Ok(())  ← gateway admits the transaction
    assert_matches!(
        validator.validate(&tx),
        Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { .. })
    );
}
```

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

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L260-271)
```rust
fn test_invalid_max_l2_gas_amount(
    #[case] rpc_tx_args: RpcTransactionArgs,
    #[case] expected_error: StatelessTransactionValidatorError,
    #[values(TransactionType::DeployAccount, TransactionType::Invoke)] tx_type: TransactionType,
) {
    let tx_validator =
        StatelessTransactionValidator { config: DEFAULT_VALIDATOR_CONFIG.to_owned() };

    let tx = rpc_tx_for_testing(tx_type, rpc_tx_args);

    assert_eq!(tx_validator.validate(&tx).unwrap_err(), expected_error);
}
```

**File:** crates/apollo_gateway_config/src/config.rs (L193-193)
```rust
            max_l2_gas_amount: 1_210_000_000,
```

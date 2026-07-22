### Title
`StatelessTransactionValidator.validate_resource_bounds` skips `max_l2_gas_amount` bound for `Declare` transactions, allowing admission of out-of-bounds resource bounds — (`File: crates/apollo_gateway/src/stateless_transaction_validator.rs`)

---

### Summary

The gateway's stateless validator enforces a `max_l2_gas_amount` upper bound on `l2_gas.max_amount` for `Invoke` and `DeployAccount` transactions, but explicitly skips this check for `Declare` transactions. Any user can submit a `Declare` transaction with `l2_gas.max_amount` set to an arbitrarily large value (up to `u64::MAX`) and it will be admitted through the gateway and into the mempool without rejection.

---

### Finding Description

In `StatelessTransactionValidator::validate_resource_bounds`, the check for `max_l2_gas_amount` is guarded by a type-check that silently skips `Declare` transactions:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
    // ← no check at all
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

The production default for `max_l2_gas_amount` is `1,210,000,000` gas units. For `Invoke` and `DeployAccount` transactions, any `l2_gas.max_amount` exceeding this value is rejected at the gateway. For `Declare` transactions, no such bound is applied. A user can set `l2_gas.max_amount = u64::MAX` on a `Declare` transaction and it will pass stateless validation, be accepted into the mempool, and be forwarded to the batcher.

This is a direct analog to the Wildcat M-10 bug: a protocol-level bound (`max_l2_gas_amount` from `StatelessTransactionValidatorConfig`) is enforced at one entry point (Invoke/DeployAccount) but not at another (Declare), breaking the invariant that the config bound applies uniformly to all transaction types.

The call path is:

1. User submits `RpcTransaction::Declare` with `resource_bounds.l2_gas.max_amount = u64::MAX`
2. `StatelessTransactionValidator::validate()` calls `validate_resource_bounds()`
3. The `if let RpcTransaction::Declare(_) = tx {}` branch is taken — the `max_l2_gas_amount` check is skipped entirely
4. The transaction passes stateless validation and proceeds to stateful validation and mempool admission

---

### Impact Explanation

**High. Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

A `Declare` transaction with `l2_gas.max_amount` far exceeding the configured `max_l2_gas_amount` bound is admitted by the gateway and enters the mempool. This violates the admission invariant that `max_l2_gas_amount` is a hard ceiling on the resource bounds a transaction may declare. The downstream blockifier caps actual step execution at `invoke_tx_max_n_steps` regardless, but the admission-layer bound — which exists to prevent mempool abuse and enforce protocol-level resource limits — is bypassed entirely for the `Declare` transaction type.

---

### Likelihood Explanation

Any unprivileged user can trigger this by submitting a well-formed `RpcDeclareTransactionV3` with an oversized `l2_gas.max_amount`. No special permissions, keys, or privileged access are required. The bypass is unconditional for all `Declare` transactions regardless of the `max_l2_gas_amount` configuration value.

---

### Recommendation

Apply the same `max_l2_gas_amount` upper-bound check to `Declare` transactions, resolving the existing TODO:

```rust
fn validate_resource_bounds(
    &self,
    tx: &RpcTransaction,
) -> StatelessTransactionValidatorResult<()> {
    if !self.config.validate_resource_bounds {
        return Ok(());
    }

    let resource_bounds = *tx.resource_bounds();
    if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0) {
        return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
    }

    if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
        return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow { ... });
    }

-   // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
-   if let RpcTransaction::Declare(_) = tx {
-   } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
+   if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
        return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { ... });
    }

    Ok(())
}
```

---

### Proof of Concept

```rust
// Construct a Declare transaction with l2_gas.max_amount = u64::MAX
let oversized_resource_bounds = AllResourceBounds {
    l2_gas: ResourceBounds {
        max_amount: GasAmount(u64::MAX),
        max_price_per_unit: GasPrice(DEFAULT_MIN_GAS_PRICE), // passes min_gas_price check
    },
    ..Default::default()
};

let config = StatelessTransactionValidatorConfig {
    validate_resource_bounds: true,
    max_l2_gas_amount: 1_210_000_000, // production default
    min_gas_price: 8_000_000_000,
    ..Default::default()
};

let validator = StatelessTransactionValidator { config };

// Declare transaction with max_amount = u64::MAX >> max_l2_gas_amount
let declare_tx = rpc_declare_tx(
    declare_tx_args!(resource_bounds: oversized_resource_bounds),
    valid_sierra_contract_class(),
);

// This passes — the max_l2_gas_amount bound is not checked for Declare
assert!(validator.validate(&declare_tx).is_ok());

// Invoke transaction with the same oversized bounds is correctly rejected
let invoke_tx = rpc_invoke_tx(invoke_tx_args!(resource_bounds: oversized_resource_bounds));
assert!(validator.validate(&invoke_tx).is_err()); // MaxGasAmountTooHigh
```

The `Declare` variant passes while the identical `Invoke` variant is rejected, demonstrating the asymmetric enforcement of the `max_l2_gas_amount` bound. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_gateway_config/src/config.rs (L192-193)
```rust
            min_gas_price: 8_000_000_000,
            max_l2_gas_amount: 1_210_000_000,
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

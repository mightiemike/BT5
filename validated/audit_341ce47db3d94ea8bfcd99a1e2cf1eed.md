### Title
Missing `max_l2_gas_amount` Upper-Bound Check for `Declare` Transactions Allows Unbounded L2 Gas Claims at Gateway Admission — (`File: crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

`StatelessTransactionValidator::validate_resource_bounds` enforces a `max_l2_gas_amount` ceiling on every transaction type **except** `Declare`. An unprivileged caller can submit a `Declare` transaction whose `l2_gas.max_amount` equals `u64::MAX`, bypassing the only gateway-level cap that prevents oversized gas claims from entering the mempool. The gap is acknowledged in a TODO comment in the production source and is confirmed by a dedicated test that asserts the over-limit value is accepted for `Declare`.

---

### Finding Description

In `validate_resource_bounds`, the check that rejects transactions whose `l2_gas.max_amount` exceeds `config.max_l2_gas_amount` is guarded by an explicit early-return for `Declare` transactions:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
    // ← no check; falls through to Ok(())
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { … });
}
``` [1](#0-0) 

The production config sets `max_l2_gas_amount = 1_210_000_000` for `Invoke` and `DeployAccount`: [2](#0-1) 

The test `valid_l2_gas_amount_on_declare` explicitly asserts that a `Declare` transaction with `l2_gas.max_amount = 200` passes even when `max_l2_gas_amount = 100`, confirming the bypass is intentional in the current code: [3](#0-2) 

The same test infrastructure shows that an identical `Invoke` or `DeployAccount` transaction **is** rejected: [4](#0-3) 

---

### Impact Explanation

**Gateway/mempool admission accepts invalid transactions (High).**

1. A `Declare` transaction with `l2_gas.max_amount = u64::MAX` passes `StatelessTransactionValidator::validate` and is forwarded to the mempool.
2. Inside the blockifier's fee pre-validation (`check_fee_bounds`), the check `max_amount >= minimal_gas_amount` trivially passes for `u64::MAX`, so the transaction is never rejected on resource grounds: [5](#0-4) 

3. `max_possible_fee` uses saturating arithmetic; with `max_amount = u64::MAX` and any non-zero `max_price_per_unit`, it saturates to `Fee(u128::MAX)`. Any downstream component that reads `max_possible_fee` for ordering, balance checks, or fee-cap enforcement receives a meaningless sentinel value. [6](#0-5) 

4. The step-limit computation in `EntryPointExecutionContext::max_steps` derives the per-transaction ceiling from `l2_gas.max_amount`; with `u64::MAX` the derived ceiling is astronomically large and is only saved by the block-level `block_upper_bound` cap — a second, unrelated guard that should not be the sole protection: [7](#0-6) 

The net result is that the gateway's own admission invariant — "no transaction may claim more than `max_l2_gas_amount` L2 gas" — is broken for `Declare` transactions, allowing them to enter the mempool and reach the batcher with an uncapped gas claim.

---

### Likelihood Explanation

**High.** The bypass requires only a well-formed `Declare` transaction with an oversized `l2_gas.max_amount` field. No special privilege, no malformed bytes, and no peer relationship is needed. The gateway is a public HTTP endpoint. The TODO comment and the dedicated test confirm the gap is known and reachable in the current production code path.

---

### Recommendation

Apply the same `max_l2_gas_amount` upper-bound check to `Declare` transactions inside `validate_resource_bounds`, removing the early-return guard:

```rust
// Remove the Declare exemption:
if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

If `Declare` transactions legitimately require a higher ceiling (e.g., because compilation is more gas-intensive), introduce a separate `max_l2_gas_amount_declare` config field rather than removing the check entirely.

---

### Proof of Concept

```
1. Build a valid RpcDeclareTransactionV3 with:
     l2_gas.max_amount      = GasAmount(u64::MAX)   // 18446744073709551615
     l2_gas.max_price_per_unit = GasPrice(min_gas_price)  // satisfies min-price check
     (all other fields valid)

2. POST to the gateway's add_transaction endpoint.

3. Observe: the gateway returns HTTP 200 / Ok(tx_hash).
   - StatelessTransactionValidator::validate_resource_bounds hits the
     `if let RpcTransaction::Declare(_) = tx { }` branch and skips the
     max_l2_gas_amount check (line 79).

4. For comparison, submit an identical RpcInvokeTransactionV3 with the same
   l2_gas.max_amount = u64::MAX.

5. Observe: the gateway returns MaxGasAmountTooHigh error (line 81-84),
   confirming the Declare path is the only unguarded one.

6. The Declare transaction is now in the mempool with max_possible_fee = Fee(u128::MAX),
   bypassing the admission invariant enforced for every other transaction type.
```

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L78-85)
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L243-271)
```rust
#[rstest]
#[case::max_l2_gas_amount_too_high(
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l2_gas: ResourceBounds {
                max_amount: GasAmount(DEFAULT_VALIDATOR_CONFIG.max_l2_gas_amount + 1),
                max_price_per_unit: GasPrice(DEFAULT_VALIDATOR_CONFIG.min_gas_price),
            },
            ..Default::default()
        },
        ..Default::default()
    },
    StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: GasAmount(DEFAULT_VALIDATOR_CONFIG.max_l2_gas_amount + 1),
        max_gas_amount: DEFAULT_VALIDATOR_CONFIG.max_l2_gas_amount
    },
)]
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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L427-458)
```rust
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

**File:** crates/starknet_api/src/transaction/fields.rs (L393-414)
```rust
    pub fn max_possible_fee(&self, tip: Tip) -> Fee {
        match self {
            ValidResourceBounds::L1Gas(l1_bounds) => {
                l1_bounds.max_amount.saturating_mul(l1_bounds.max_price_per_unit)
            }
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => l1_gas
                .max_amount
                .saturating_mul(l1_gas.max_price_per_unit)
                .saturating_add(
                    l2_gas
                        .max_amount
                        .saturating_mul(l2_gas.max_price_per_unit.saturating_add(tip.into())),
                )
                .saturating_add(
                    l1_data_gas.max_amount.saturating_mul(l1_data_gas.max_price_per_unit),
                ),
        }
    }
```

**File:** crates/blockifier/src/execution/entry_point.rs (L451-461)
```rust
                ValidResourceBounds::AllResources(AllResourceBounds {
                    l2_gas: ResourceBounds { max_amount, .. },
                    ..
                }) => {
                    if l2_gas_per_step.is_zero() {
                        u64::MAX
                    } else {
                        max_amount.0.saturating_div(l2_gas_per_step)
                    }
                }
            },
```

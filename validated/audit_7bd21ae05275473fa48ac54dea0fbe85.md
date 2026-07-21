### Title
Declare Transactions Bypass `max_l2_gas_amount` Admission Gate, Allowing Unbounded L2 Gas Claims Through the Gateway - (File: `crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary
The stateless gateway validator explicitly skips the `max_l2_gas_amount` upper-bound check for `Declare` transactions. Any unprivileged user can submit a `Declare` transaction whose `l2_gas.max_amount` exceeds the configured ceiling (`1_210_000_000` by default). The gateway admits the transaction, it enters the mempool, and the batcher attempts to execute it — breaking the admission invariant that all transactions must satisfy `l2_gas.max_amount ≤ max_l2_gas_amount` before sequencing.

### Finding Description

In `validate_resource_bounds` the check for `max_l2_gas_amount` is guarded by an empty `if`-branch that silently skips the entire check for `Declare` transactions:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
    // ← nothing; check is skipped
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { … });
}
``` [1](#0-0) 

The same function correctly enforces the `min_gas_price` floor for all transaction types including `Declare`, so the asymmetry is not intentional design — it is a missing gate confirmed by the inline TODO. [2](#0-1) 

The default ceiling is `1_210_000_000` L2 gas units: [3](#0-2) 

The production gateway config also sets this value: [4](#0-3) 

A companion test (`valid_l2_gas_amount_on_declare`) explicitly asserts that a `Declare` transaction with `l2_gas.max_amount` **above** the limit passes validation, confirming the bypass is reachable: [5](#0-4) 

The `validate_tx_extended_calldata_size` function also returns `Ok(())` immediately for `Declare` transactions, so there is no secondary size gate that would catch an overs

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L71-85)
```rust
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
```

**File:** crates/apollo_gateway_config/src/config.rs (L188-203)
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
```

**File:** crates/apollo_deployments/resources/app_configs/replacer_gateway_config.json (L25-25)
```json
  "gateway_config.static_config.stateless_tx_validator_config.max_l2_gas_amount": 1210000000,
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

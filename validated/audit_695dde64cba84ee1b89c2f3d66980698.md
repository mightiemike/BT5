### Title
`max_l2_gas_amount` Limit Unenforced for Declare Transactions Allows Gateway Admission Bypass - (File: `crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

The `StatelessTransactionValidator` enforces a per-transaction `max_l2_gas_amount` ceiling for Invoke and DeployAccount transactions but explicitly skips this check for Declare transactions. An attacker can submit a `RpcDeclareTransaction::V3` with `l2_gas.max_amount` set to an arbitrarily large value (e.g., `u64::MAX`) and it will pass all gateway admission checks, enter the mempool, and be executed with that inflated gas limit.

### Finding Description

In `StatelessTransactionValidator::validate_resource_bounds`, the check for `l2_gas.max_amount > config.max_l2_gas_amount` is guarded by a type-discriminant branch that silently skips the entire check for Declare transactions:

```rust
// crates/apollo_gateway/src/stateless_transaction_validator.rs, lines 78-85
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

The production default for `max_l2_gas_amount` is `1_210_000_000` (`StatelessTransactionValidatorConfig::default()`). For Invoke and DeployAccount transactions this ceiling is enforced. For Declare transactions it is not. The existing test `valid_l2_gas_amount_on_declare` explicitly confirms and exercises this bypass path: a Declare transaction with `l2_gas.max_amount = 200` passes when `max_l2_gas_amount = 100`.

The `validate_resource_bounds` flag (shared pointer target across gateway stateless, gateway stateful, and mempool configs) does not help here: even when `validate_resource_bounds = true`, the Declare branch is a no-op.

The analog to the external report is exact: the collateral-value limit check was enforced on deposit/borrow but not on withdrawal; here the `max_l2_gas_amount` limit is enforced on Invoke/DeployAccount but not on Declare.

### Impact Explanation

**High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

A Declare transaction with `l2_gas.max_amount = u64::MAX` (or any value above `max_l2_gas_amount`):

1. Passes `StatelessTransactionValidator::validate_resource_bounds` without error.
2. Passes `StatefulTransactionValidator::validate_resource_bounds` (which only checks the L2 gas *price*, not the amount).
3. Enters the mempool and is forwarded to the batcher.
4. During blockifier execution the declared `max_amount` is used as the per-transaction gas limit; the transaction can consume up to the block's global L2 gas budget in a single transaction, bypassing the intended per-transaction ceiling.

This allows a single Declare transaction to monopolise an entire block's L2 gas allocation, starving other transactions and degrading sequencer throughput.

### Likelihood Explanation

Any unprivileged user can craft a `starknet_addDeclareTransaction` RPC call with an oversized `l2_gas.max_amount`. No special account, key, or privilege is required. The bypass is unconditional and deterministic.

### Recommendation

Remove the type-discriminant exemption and apply the same `max_l2_gas_amount` ceiling to Declare transactions:

```rust
// Remove the Declare exemption branch entirely:
if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

If Declare transactions legitimately require a higher gas ceiling (e.g., for large Sierra programs), introduce a separate `max_l2_gas_amount_declare` config parameter rather than removing the check entirely.

### Proof of Concept

The existing test `valid_l2_gas_amount_on_declare` already demonstrates the bypass:

```rust
// crates/apollo_gateway/src/stateless_transaction_validator_test.rs, lines 173-201
#[case::l2_gas_amount_out_of_limit(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        max_l2_gas_amount: 100,          // limit = 100
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l2_gas: ResourceBounds {
                max_amount: GasAmount(200), // declared = 200 > 100
                ..NON_EMPTY_RESOURCE_BOUNDS
            },
            ..Default::default()
        },
        ..Default::default()
    }
)]
fn valid_l2_gas_amount_on_declare(...) {
    let tx_type = TransactionType::Declare;
    // ...
    assert_matches!(tx_validator.validate(&tx), Ok(())); // passes — limit bypassed
}
```

To confirm the production impact, submit a `RpcDeclareTransaction::V3` with `resource_bounds.l2_gas.max_amount = GasAmount(u64::MAX)` to a node with default config (`max_l2_gas_amount = 1_210_000_000`). The gateway will accept it, the mempool will queue it, and the blockifier will execute it with a gas limit of `u64::MAX`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_node_config/src/node_config.rs (L157-168)
```rust
        (
            ser_pointer_target_param(
                "validate_resource_bounds",
                &true,
                "Indicates that validations related to resource bounds are applied. \
                It should be set to false during a system bootstrap.",
            ),
            set_pointing_param_paths(&[
                "gateway_config.static_config.stateful_tx_validator_config.validate_resource_bounds",
                "gateway_config.static_config.stateless_tx_validator_config.validate_resource_bounds",
                "mempool_config.static_config.validate_resource_bounds",
            ]),
```

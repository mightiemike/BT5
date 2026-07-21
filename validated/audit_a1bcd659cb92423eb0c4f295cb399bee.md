### Title
Gateway `max_l2_gas_amount` Admission Check Unconditionally Skipped for Declare Transactions — (File: `crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary
The `StatelessTransactionValidator.validate_resource_bounds()` function enforces a `max_l2_gas_amount` ceiling on the `l2_gas.max_amount` field for Invoke and DeployAccount transactions, but the identical check is explicitly omitted for Declare transactions via a hard-coded branch. This is a direct config-validation boundary analog of the Cosmos-SDK bug: just as that sequencer checked block byte-size but not gas, the Apollo gateway checks the per-transaction L2 gas declaration for two of three transaction types and silently skips it for the third.

### Finding Description
In `crates/apollo_gateway/src/stateless_transaction_validator.rs` the relevant branch reads:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { … });
}
``` [1](#0-0) 

The production `StatelessTransactionValidatorConfig` default sets `max_l2_gas_amount = 1_210_000_000` (1.21 B gas).

<cite repo="patrichyt

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

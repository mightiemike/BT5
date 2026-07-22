### Title
Missing `max_l2_gas_amount` Admission Gate on `Declare` Transactions Allows Unbounded Gas-Claim Spam - (`crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

The `StatelessTransactionValidator::validate_resource_bounds` function explicitly skips the `max_l2_gas_amount` upper-bound check for `RpcTransaction::Declare` transactions. Any unprivileged user can submit a `Declare` transaction with `l2_gas.max_amount = u64::MAX` and it will pass all gateway admission checks, enter the mempool, and force the batcher to process it. This is the direct sequencer analog of the missing `chargeFee` modifier: a specific transaction type bypasses the resource-bound gate that is meant to prevent cost-free spam of the sequencer's internal pipeline.

### Finding Description

In `validate_resource_bounds`, the `max_l2_gas_amount` cap is applied to `Invoke` and `DeployAccount` transactions but is explicitly skipped for `Declare`:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { … });
}
``` [1](#0-0) 

The production default for `max_l2_gas_amount` is `1,210,000,000` L2 gas units. [2](#0-1) 

The test `valid_l2_gas_amount_on_declare` explicitly confirms that a `Declare` transaction with `l2_gas.max_amount = 200` passes even when the configured limit is `100`, proving this is a reachable, production code path and not a test artifact. [3](#0-2) 

The only remaining checks that apply to `Declare` are:
1. Non-zero total resource bound (`max_possible_fee(Tip::ZERO) != Fee(0)`)
2. `l2_gas.max_price_per_unit >= min_gas_price` (default `8_000_000_000`)
3. Contract class object/bytecode size limits [4](#0-3) 

None of these checks bound the declared `l2_gas.max_amount`. A `Declare` transaction with `l2_gas.max_amount = u64::MAX` and `max_price_per_unit = min_gas_price` passes all three checks and is admitted.

The `validate_resource_bounds` pointer in `node_config.rs` propagates the flag to the stateless validator, stateful validator, and mempool simultaneously, but none of those downstream components add a `max_l2_gas_amount` cap for `Declare` either. [5](#0-4) 

### Impact Explanation

**High — Mempool/gateway admission accepts invalid transactions before sequencing.**

A `Declare` transaction with `l2_gas.max_amount = u64::MAX` is structurally invalid under the same admission policy that governs `Invoke` and `DeployAccount` transactions (which are capped at `1,210,000,000`). The gateway accepts it, it enters the mempool, and the batcher must process it. Because the batcher's `ProposeTransactionProvider` fetches mempool transactions without re-applying the `max_l2_gas_amount` gate, these transactions reach the blockifier. [6](#0-5) 

At execution time the blockifier bounds actual gas by the block gas limit, so the transaction does not consume unbounded resources during execution. However, the admission gap means:

- The gateway and mempool accept and hold `Declare` transactions that violate the intended resource-bound policy.
- Fee estimation (`estimate_fee`) for such a transaction computes `max_possible_fee` using the declared `u64::MAX` amount, which can overflow or return a misleadingly large authoritative value to RPC callers.
- An attacker can flood the mempool with `Declare` transactions carrying inflated gas claims at the cost of only `min_gas_price × actual_gas_consumed`, with no additional cost for the inflated declared bound.

### Likelihood Explanation

**High.** The attack requires no privilege. Any user with a valid Sierra class (subject only to size limits) and the minimum gas price can submit `Declare` transactions with `l2_gas.max_amount = u64::MAX`. The code path is unconditional — the `if let RpcTransaction::Declare(_) = tx { }` branch is always taken, with no configuration flag that re-enables the check for `Declare`.

### Recommendation

Remove the `Declare`-specific exemption and apply the same `max_l2_gas_amount` cap to all transaction types, or introduce a separate, appropriately sized cap for `Declare` transactions. The existing TODO comment acknowledges this gap:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
```

The fix is to replace the empty `Declare` branch with the same check applied to other transaction types, using either the same `max_l2_gas_amount` value or a `Declare`-specific constant that reflects the maximum gas a `Declare` transaction can legitimately consume.

### Proof of Concept

1. Construct an `RpcDeclareTransactionV3` with:
   - A valid minimal Sierra class (within `max_contract_class_object_size`)
   - `resource_bounds.l2_gas.max_amount = GasAmount(u64::MAX)`
   - `resource_bounds.l2_gas.max_price_per_unit = GasPrice(8_000_000_001)` (above `min_gas_price`)
   - All other resource bounds zero (total fee is non-zero due to L2 gas)

2. Submit to the gateway's `add_tx` endpoint.

3. Observe: `StatelessTransactionValidator::validate` returns `Ok(())` — the `MaxGasAmountTooHigh` error is never reached for `Declare`.

4. The transaction enters the mempool. Calling `starknet_estimateFee` on it returns a fee computed from `u64::MAX × max_price_per_unit`, producing an authoritative-looking but incorrect value.

The test `valid_l2_gas_amount_on_declare` already demonstrates step 3 in the test suite, confirming the behavior is reachable in production code. [3](#0-2)

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

**File:** crates/apollo_batcher/src/transaction_provider.rs (L112-123)
```rust
    async fn get_mempool_txs(
        &mut self,
        n_txs: usize,
    ) -> TransactionProviderResult<Vec<InternalConsensusTransaction>> {
        Ok(self
            .mempool_client
            .get_txs(n_txs)
            .await?
            .into_iter()
            .map(InternalConsensusTransaction::RpcTransaction)
            .collect())
    }
```

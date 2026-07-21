### Title
Gateway L2 Gas Price Threshold Uses Stale Previous-Block Price, Causing Wrong Admission Decisions - (File: crates/apollo_gateway/src/stateful_transaction_validator.rs)

### Summary

`StatefulTransactionValidator::validate_resource_bounds` computes the admission threshold for a transaction's L2 gas `max_price_per_unit` using the **previous block's** L2 gas price. The next block's L2 gas price — the price that will actually govern execution — is computed by an EIP-1559 formula and can diverge from the previous block's price. The developer acknowledged this with an explicit TODO. The result is a systematic mismatch between the price used for admission and the price used for execution, causing the gateway to either admit transactions that will be rejected by the batcher or reject transactions that are valid for the next block.

### Finding Description

In `validate_resource_bounds`, the gateway reads the L2 gas price from the **last accepted block** and uses it as the reference for the admission threshold:

```rust
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
``` [1](#0-0) 

The threshold is then computed as:

```rust
let threshold = (gas_price_threshold_multiplier * previous_block_l2_gas_price.get().0).to_integer();
if tx_l2_gas_price.0 < threshold {
    return Err(...GAS_PRICE_TOO_LOW...);
}
``` [2](#0-1) 

The **next** block's L2 gas price is computed separately by `calculate_next_l2_gas_price_for_fin` using EIP-1559 mechanics:

```rust
pub fn calculate_next_base_gas_price(
    price: GasPrice,
    gas_used: GasAmount,
    gas_target: GasAmount,
    min_gas_price: GasPrice,
) -> GasPrice { ... }
``` [3](#0-2) 

The next block's price can be higher or lower than the previous block's price depending on whether `gas_used > gas_target` or `gas_used < gas_target`. The gateway's admission check is blind to this divergence.

The blockifier's pre-validation enforces the **actual** block gas price at execution time:

```rust
if resource_bounds.max_price_per_unit < actual_gas_price.get() {
    insufficiencies_resource.push(
        ResourceBoundsError::MaxGasPriceTooLow { ... }
    );
}
``` [4](#0-3) 

This creates a two-gate system where the gates use different reference prices.

### Impact Explanation

**Scenario A — Congestion increasing (next block price > previous block price):**
A transaction with `max_price_per_unit = threshold` (based on the previous block's lower price) passes the gateway check but is below the actual next-block price. The batcher rejects it at pre-validation with `MaxGasPriceTooLow`. The gateway has admitted an invalid transaction into the mempool.

**Scenario B — Congestion decreasing (next block price < previous block price):**
A transaction with `max_price_per_unit = next_block_price` is valid for execution but falls below `min_gas_price_percentage% × previous_block_price`. The gateway rejects it with `GAS_PRICE_TOO_LOW` even though the batcher would accept it. A valid transaction is incorrectly denied sequencing.

Both scenarios match: **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

### Likelihood Explanation

The EIP-1559 formula adjusts the L2 gas price every block based on utilization. Any block that is not exactly at the gas target causes the next block's price to diverge from the current block's price. Under normal network load variation this divergence occurs continuously. The trigger requires no privilege — any user submitting a V3 invoke transaction with `AllResourceBounds` goes through this path. The `validate_resource_bounds` flag defaults to enabled in production config.

### Recommendation

Replace the stale previous-block price with the computed next-block L2 gas price. The sequencer already computes this value via `calculate_next_l2_gas_price_for_fin`; it should be made available to the gateway's stateful validator (e.g., stored in the block header as `next_l2_gas_price`, which the TODO comment already anticipates). The admission threshold should then be:

```rust
let next_block_l2_gas_price = self
    .gateway_fixed_block_state_reader
    .get_block_info()
    .await?
    .next_l2_gas_price; // use the forward-looking price
```

This mirrors the fix recommended in the Teller Finance report: include the "new amount" (here, the price change caused by the current block's gas usage) in the calculation rather than using the stale current-state value.

### Proof of Concept

1. Assume `min_gas_price_percentage = 100`, previous block L2 gas price = 1000 fri, gas target = 50% of max block size, previous block was at 10% utilization (well below target).
2. EIP-1559 computes next block price ≈ `1000 - (1000 × (50% - 10%) / (50% × denominator))` = significantly lower, e.g. 875 fri.
3. User submits a V3 invoke with `l2_gas.max_price_per_unit = 900 fri`.
4. Gateway threshold = `100% × 1000 = 1000`. Since `900 < 1000`, the gateway returns `GAS_PRICE_TOO_LOW` and rejects the transaction.
5. The batcher would have accepted this transaction at 875 fri. A valid transaction is incorrectly denied sequencing.

Conversely, if the previous block was at 90% utilization, the next block price rises to ~1125 fri. A transaction admitted at 1000 fri (exactly at threshold) would be rejected by the batcher at pre-validation with `MaxGasPriceTooLow`, having been incorrectly admitted by the gateway.

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L223-241)
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

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L86-140)
```rust
pub fn calculate_next_base_gas_price(
    price: GasPrice,
    gas_used: GasAmount,
    gas_target: GasAmount,
    min_gas_price: GasPrice,
) -> GasPrice {
    let versioned_constants = VersionedConstants::latest_constants();
    assert!(
        gas_target < versioned_constants.max_block_size,
        "Gas target must be lower than max block size."
    );
    assert!(gas_target.0 > 0, "Gas target must be greater than zero.");
    assert!(
        versioned_constants.gas_price_max_change_denominator > 0,
        "Denominator constant must be greater than zero."
    );

    // If the current price is below the minimum, apply a gradual adjustment and return early.
    // This allows the price to increase by at most 1/MIN_GAS_PRICE_INCREASE_DENOMINATOR per block.
    if price < min_gas_price {
        let max_increase = price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR;
        let adjusted = price.0 + max_increase;
        // Cap at min_gas_price to avoid overshooting
        let adjusted_price = adjusted.min(min_gas_price.0);
        info!(
            "Fee Market: Price {} below minimum gas price {}, adjusted price: {} )",
            price.0, min_gas_price.0, adjusted_price
        );
        return GasPrice(adjusted_price);
    }

    // Use U256 to avoid overflow, as multiplying a u128 by a u64 remains within U256 bounds.
    let gas_delta = U256::from(gas_used.0.abs_diff(gas_target.0));
    let gas_target_u256 = U256::from(gas_target.0);
    let price_u256 = U256::from(price.0);

    // Calculate price change by multiplying first, then dividing. This avoids the precision loss
    // that occurs when dividing before multiplying.
    let denominator =
        gas_target_u256 * U256::from(versioned_constants.gas_price_max_change_denominator);
    let price_change = (price_u256 * gas_delta) / denominator;

    let adjusted_price_u256 =
        if gas_used > gas_target { price_u256 + price_change } else { price_u256 - price_change };

    // Sanity check: ensure direction of change is correct
    assert!(
        gas_used > gas_target && adjusted_price_u256 >= price_u256
            || gas_used <= gas_target && adjusted_price_u256 <= price_u256
    );

    // Price should not realistically exceed u128::MAX, bound to avoid theoretical overflow.
    let adjusted_price = u128::try_from(adjusted_price_u256).unwrap_or(u128::MAX);
    GasPrice(max(adjusted_price, min_gas_price.0))
}
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L441-449)
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
```

### Title
Gateway Admission Validates L2 Gas Price Against Stale `l2_gas_price` Instead of `next_l2_gas_price`, Causing Incorrect Transaction Admission/Rejection — (File: `crates/apollo_gateway/src/stateful_transaction_validator.rs`)

---

### Summary

The `validate_resource_bounds` function in `StatefulTransactionValidator` checks a transaction's `max_price_per_unit` against the **current committed block's** `l2_gas_price`. However, the transaction will be executed in the **next block**, which uses `next_l2_gas_price` — a distinct EIP-1559-derived value stored in the same block header. This off-by-one price boundary causes the gateway to reject valid transactions (when gas price is falling) and admit transactions that will fail at execution time (when gas price is rising). A developer TODO in the code explicitly acknowledges the wrong field is being read.

---

### Finding Description

In `validate_resource_bounds`, the gateway reads the latest committed block's `gas_prices.strk_gas_prices.l2_gas_price` and uses it as the admission threshold:

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

The threshold computation is:

```rust
let threshold = (gas_price_threshold_multiplier
    * previous_block_l2_gas_price.get().0)
    .to_integer();
if tx_l2_gas_price.0 < threshold {
    return Err(StarknetError { ... });
}
``` [2](#0-1) 

With the production default of `min_gas_price_percentage = 100`, the admission gate is:

```
tx.l2_gas.max_price_per_unit  >=  current_block.l2_gas_price
``` [3](#0-2) 

But the block the transaction will actually be included in uses `next_l2_gas_price`, which is the EIP-1559-derived price stored alongside `l2_gas_price` in every block header:

```rust
pub struct StorageBlockHeader {
    pub l2_gas_price: GasPricePerToken,      // price for THIS block
    pub next_l2_gas_price: GasPrice,          // price for the NEXT block
    ...
}
``` [4](#0-3) 

The EIP-1559 mechanism in `calculate_next_base_gas_price` adjusts the price each block based on gas usage vs. target, so `next_l2_gas_price` routinely diverges from `l2_gas_price`: [5](#0-4) 

The `StatefulTransactionValidatorConfig` is shared via a pointer target across the gateway and mempool: [6](#0-5) 

---

### Impact Explanation

**Scenario A — Gas price falling (`next_l2_gas_price < l2_gas_price`):**

| Value | Amount |
|---|---|
| `current_block.l2_gas_price` | 100 |
| `current_block.next_l2_gas_price` | 90 |
| User's `max_price_per_unit` | 95 |

Gateway check: `95 >= 100` → **REJECTED**. But the transaction would succeed at execution time since `95 >= 90`. A valid transaction is denied admission.

**Scenario B — Gas price rising (`next_l2_gas_price > l2_gas_price`):**

| Value | Amount |
|---|---|
| `current_block.l2_gas_price` | 100 |
| `current_block.next_l2_gas_price` | 110 |
| User's `max_price_per_unit` | 105 |

Gateway check: `105 >= 100` → **ADMITTED**. But the transaction will fail at execution time since `105 < 110`. An invalid transaction enters the mempool.

This matches the allowed impact: **High — Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

The `validate_resource_bounds` flag is `true` by default in production config: [7](#0-6) 

---

### Likelihood Explanation

The EIP-1559 mechanism adjusts `next_l2_gas_price` every block based on gas usage relative to `gas_target`. Any block that is not exactly at target utilization produces a `next_l2_gas_price ≠ l2_gas_price`. This is the normal operating condition of the network, not an edge case. The magnitude of the discrepancy per block is bounded by `1 / gas_price_max_change_denominator`, but it is always present during normal operation. The TODO comment in the production code confirms the developers are aware the wrong field is being read.

---

### Recommendation

Replace the read of `l2_gas_price` with `next_l2_gas_price` from the block header, as the TODO comment already prescribes:

```rust
// Before (wrong):
let previous_block_l2_gas_price = self
    .gateway_fixed_block_state_reader
    .get_block_info()
    .await?
    .gas_prices
    .strk_gas_prices
    .l2_gas_price;

// After (correct):
let next_block_l2_gas_price = self
    .gateway_fixed_block_state_reader
    .get_block_header()   // or equivalent accessor
    .await?
    .next_l2_gas_price;
```

The admission threshold should be computed against the price the transaction will actually face at execution time, not the price of the already-committed block.

---

### Proof of Concept

1. Observe that `StorageBlockHeader` stores both `l2_gas_price` (current block) and `next_l2_gas_price` (next block). [4](#0-3) 

2. Observe that `calculate_next_base_gas_price` produces a different value each block whenever gas usage ≠ gas target. [5](#0-4) 

3. Observe that `validate_resource_bounds` reads `l2_gas_price` (not `next_l2_gas_price`) and the TODO comment acknowledges the bug. [1](#0-0) 

4. Construct a transaction with `max_price_per_unit = next_l2_gas_price - 1` when gas price is rising. The gateway admits it (`max_price_per_unit >= l2_gas_price`), but the blockifier will revert it at execution time (`max_price_per_unit < next_l2_gas_price`).

5. Construct a transaction with `max_price_per_unit = l2_gas_price - 1` when gas price is falling. The gateway rejects it even though `max_price_per_unit >= next_l2_gas_price` and execution would succeed.

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L227-241)
```rust
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

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L367-383)
```rust
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
```

**File:** crates/apollo_gateway_config/src/config.rs (L289-299)
```rust
impl Default for StatefulTransactionValidatorConfig {
    fn default() -> Self {
        StatefulTransactionValidatorConfig {
            validate_resource_bounds: true,
            max_allowed_nonce_gap: 200,
            reject_future_declare_txs: true,
            max_nonce_for_validation_skip: Nonce(Felt::ONE),
            min_gas_price_percentage: 100,
            versioned_constants_overrides: None,
        }
    }
```

**File:** crates/apollo_storage/src/header.rs (L85-90)
```rust
    pub l2_gas_price: GasPricePerToken,
    /// The amount of L2 gas consumed.
    pub l2_gas_consumed: GasAmount,
    /// The next L2 gas price.
    pub next_l2_gas_price: GasPrice,
    /// The state root after this block.
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L86-139)
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

**File:** crates/apollo_node/resources/config_schema.json (L4127-4131)
```json
  "validate_resource_bounds": {
    "description": "Indicates that validations related to resource bounds are applied. It should be set to false during a system bootstrap.",
    "privacy": "TemporaryValue",
    "value": true
  },
```

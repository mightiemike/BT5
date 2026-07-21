### Title
Gateway `validate_resource_bounds` checks L2 gas price against stale previous-block price, admitting transactions that will fail execution - (`crates/apollo_gateway/src/stateful_transaction_validator.rs`)

### Summary

`StatefulTransactionValidator::validate_resource_bounds` validates a transaction's `max_price_per_unit` against the **previous block's** L2 gas price. Under EIP-1559, the next block's price is computed from the previous block's gas usage and can be strictly higher. Transactions with `max_price_per_unit` in the range `[P_prev, P_next)` pass full gateway validation — including the blockifier `__validate__` entry point — but fail pre-validation when the batcher executes them in the next block. The gateway therefore admits transactions that are invalid for the block they will actually be sequenced in.

### Finding Description

In `validate_resource_bounds`, the gateway reads the L2 gas price from `gateway_fixed_block_state_reader.get_block_info()`, which returns the **previous committed block's** gas prices. The code itself acknowledges the gap with a TODO:

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

The same stale `BlockInfo` is reused in `run_validate_entry_point` to build the `BlockContext` for the blockifier's `perform_pre_validation_stage`:

```rust
let mut block_info = self.gateway_fixed_block_state_reader.get_block_info().await?;
block_info.block_number = block_info.block_number.unchecked_next();
let block_context = BlockContext::new(block_info, self.chain_info.clone(), versioned_constants, BouncerConfig::max());
``` [2](#0-1) 

Because `GatewayFixedBlockSyncStateClient` uses a `OnceCell<BlockInfo>` that is populated once per validator instance, both calls return the same previous-block price: [3](#0-2) 

The next block's L2 gas price is computed by EIP-1559 in `calculate_next_l2_gas_price_for_fin`: [4](#0-3) 

When the previous block is at high utilization, `P_next = calculate_next_base_gas_price(P_prev, gas_used, gas_target, min)` yields `P_next > P_prev`. The block hash computation confirms `next_l2_gas_price` is a distinct field stored in `fee_market_info`, separate from the block header's `l2_gas_price`: [5](#0-4) 

The batcher reads gas prices from the committed block header for the block it is building, which will reflect `P_next`. The blockifier's `perform_pre_validation_stage` then checks `max_price_per_unit >= actual_gas_price` using `P_next`, causing the transaction to fail: [6](#0-5) 

### Impact Explanation

The gateway performs a complete stateful validation — including the `__validate__` Cairo entry point — using stale gas prices, then admits the transaction to the mempool. When the batcher later executes the transaction in the next block with the actual (higher) `P_next`, the transaction fails `perform_pre_validation_stage`. The mempool is polluted with transactions that are guaranteed to fail execution. This matches: **High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

### Likelihood Explanation

Any period of sustained high L2 utilization causes EIP-1559 to raise `P_next` above `P_prev`. An attacker observing the previous block's utilization can predict `P_next` and craft transactions with `max_price_per_unit = P_prev` that will pass gateway admission but fail batcher execution. The `min_gas_price_percentage` config (default 100, but configurable to lower values) widens the exploitable window further. [7](#0-6) 

### Recommendation

Replace the use of `previous_block_l2_gas_price` with the `next_l2_gas_price` value stored in the block's fee market info (as the TODO comment already notes). This requires exposing `next_l2_gas_price` through the state sync API and `GatewayFixedBlockStateReader`, so that `validate_resource_bounds` and `run_validate_entry_point` both use the price that will actually govern the next block's execution.

### Proof of Concept

1. Block N has `l2_gas_price = P_N` and was at 100% utilization.
2. EIP-1559 computes `P_{N+1} = P_N * (1 + 1/gas_price_max_change_denominator) > P_N`.
3. Attacker submits a transaction with `max_price_per_unit = P_N`.
4. `validate_resource_bounds` checks `P_N >= 100% * P_N` → **passes**.
5. `run_validate_entry_point` builds `BlockContext` with `gas_prices.l2_gas_price = P_N`; blockifier `perform_pre_validation_stage` checks `P_N >= P_N` → **passes**.
6. Transaction is admitted to the mempool.
7. Batcher builds block N+1 with `l2_gas_price = P_{N+1}`.
8. Batcher's blockifier checks `P_N >= P_{N+1}` → **fails** with `MaxGasPriceTooLow`.
9. Transaction is rejected at execution time; mempool slot was wasted.

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L223-243)
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
        Ok(())
    }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L323-330)
```rust
        let mut block_info = self.gateway_fixed_block_state_reader.get_block_info().await?;
        block_info.block_number = block_info.block_number.unchecked_next();
        let block_context = BlockContext::new(
            block_info,
            self.chain_info.clone(),
            versioned_constants,
            BouncerConfig::max(),
        );
```

**File:** crates/apollo_gateway/src/gateway_fixed_block_state_reader.rs (L19-67)
```rust
pub struct GatewayFixedBlockSyncStateClient {
    state_sync_client: SharedStateSyncClient,
    block_number: BlockNumber,
    block_info_cache: OnceCell<BlockInfo>,
}

impl GatewayFixedBlockSyncStateClient {
    pub fn new(state_sync_client: SharedStateSyncClient, block_number: BlockNumber) -> Self {
        Self { state_sync_client, block_number, block_info_cache: OnceCell::new() }
    }

    async fn get_block_info_from_sync_client(&self) -> StarknetResult<BlockInfo> {
        let block = self.state_sync_client.get_block(self.block_number).await.map_err(|e| {
            StarknetError::internal_with_logging("Failed to get latest block info", e)
        })?;

        let block_header = block.block_header_without_hash;
        let block_info = BlockInfo {
            block_number: block_header.block_number,
            block_timestamp: block_header.timestamp,
            sequencer_address: block_header.sequencer.0,
            gas_prices: GasPrices {
                eth_gas_prices: GasPriceVector {
                    l1_gas_price: block_header.l1_gas_price.price_in_wei.try_into()?,
                    l1_data_gas_price: block_header.l1_data_gas_price.price_in_wei.try_into()?,
                    l2_gas_price: block_header.l2_gas_price.price_in_wei.try_into()?,
                },
                strk_gas_prices: GasPriceVector {
                    l1_gas_price: block_header.l1_gas_price.price_in_fri.try_into()?,
                    l1_data_gas_price: block_header.l1_data_gas_price.price_in_fri.try_into()?,
                    l2_gas_price: block_header.l2_gas_price.price_in_fri.try_into()?,
                },
            },
            use_kzg_da: block_header.l1_da_mode.is_use_kzg_da(),
            starknet_version: block_header.starknet_version,
        };

        Ok(block_info)
    }
}

#[async_trait]
impl GatewayFixedBlockStateReader for GatewayFixedBlockSyncStateClient {
    async fn get_block_info(&self) -> StarknetResult<BlockInfo> {
        self.block_info_cache
            .get_or_try_init(|| self.get_block_info_from_sync_client())
            .await
            .cloned()
    }
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L55-77)
```rust
pub fn calculate_next_l2_gas_price_for_fin(
    current_l2_gas_price: GasPrice,
    height: BlockNumber,
    l2_gas_used: GasAmount,
    override_l2_gas_price_fri: Option<u128>,
    min_l2_gas_price_per_height: &[PricePerHeight],
    fee_actual: Option<GasPrice>,
) -> GasPrice {
    if let Some(override_value) = override_l2_gas_price_fri {
        info!(
            "L2 gas price ({}) is not updated, remains on override value of {override_value} fri",
            current_l2_gas_price.0
        );
        return GasPrice(override_value);
    }
    let gas_target = VersionedConstants::latest_constants().gas_target;
    let config_min = get_min_gas_price_for_height(height, min_l2_gas_price_per_height);
    let effective_min = match fee_actual {
        Some(fa) => GasPrice(max(config_min.0, fa.0)),
        None => config_min,
    };
    calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
}
```

**File:** echonet/echo_center.py (L679-682)
```python
                    "l2_gas_price": block_info["l2_gas_price"],
                    "l2_gas_consumed": fee_market_info["l2_gas_consumed"],
                    "next_l2_gas_price": fee_market_info["next_l2_gas_price"],
                    "state_root": state_root,
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L441-458)
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

**File:** crates/apollo_gateway_config/src/config.rs (L285-299)
```rust
    // Minimum gas price as percentage of threshold to accept transactions.
    pub min_gas_price_percentage: u8, // E.g., 80 to require 80% of threshold.
}

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

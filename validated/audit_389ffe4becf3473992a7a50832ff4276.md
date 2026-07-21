### Title
Gateway Admission Validates L2 Gas Price Against Stale Previous-Block Price Instead of Next-Block Price - (File: crates/apollo_gateway/src/stateful_transaction_validator.rs)

### Summary
`StatefulTransactionValidator::validate_resource_bounds` checks a transaction's `max_price_per_unit` against the **previous committed block's** L2 gas price, while actual execution uses the **next block's** L2 gas price computed via the EIP-1559 formula. This systematic divergence causes the gateway to accept transactions that will be excluded from the next block (when gas price is rising) and to reject transactions that would be valid for the next block (when gas price is falling). A self-acknowledged TODO in the source confirms the wrong reference value is used.

### Finding Description

In `validate_resource_bounds`, the threshold is derived from `gateway_fixed_block_state_reader.get_block_info()`:

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

`gateway_fixed_block_state_reader` is a `GatewayFixedBlockSyncStateClient` constructed with the latest committed block number. Its `get_block_info` implementation uses a `OnceCell<BlockInfo>` that is populated once from state-sync and never refreshed:

```rust
pub struct GatewayFixedBlockSyncStateClient {
    state_sync_client: SharedStateSyncClient,
    block_number: BlockNumber,
    block_info_cache: OnceCell<BlockInfo>,
}
...
async fn get_block_info(&self) -> StarknetResult<BlockInfo> {
    self.block_info_cache
        .get_or_try_init(|| self.get_block_info_from_sync_client())
        .await
        .cloned()
}
``` [2](#0-1) 

The actual L2 gas price for the **next** block is computed by `calculate_next_l2_gas_price_for_fin` using the EIP-1559 formula:

```rust
pub fn calculate_next_l2_gas_price_for_fin(
    current_l2_gas_price: GasPrice,
    height: BlockNumber,
    l2_gas_used: GasAmount,
    ...
) -> GasPrice {
    ...
    calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
}
``` [3](#0-2) 

The next-block price diverges from the previous-block price by up to `1 / gas_price_max_change_denominator` per block, a parameter defined in the consensus `VersionedConstants`: [4](#0-3) 

The same stale `block_info` (with previous-block gas prices but incremented block number) is also passed into `run_validate_entry_point`, so the `__validate__` entry point executes under the wrong gas-price environment:

```rust
let mut block_info = self.gateway_fixed_block_state_reader.get_block_info().await?;
block_info.block_number = block_info.block_number.unchecked_next();
let block_context = BlockContext::new(block_info, ...);
``` [5](#0-4) 

### Impact Explanation

**Accepts invalid transactions (gas price rising):** When block N is full and `P_{N+1} > P_N`, a transaction with `max_price_per_unit = P_N` passes the gateway check (`P_N >= 100% × P_N`) but is excluded from block N+1 because `max_price_per_unit < P_{N+1}`. The transaction enters the mempool and occupies it until expiry.

**Rejects valid transactions (gas price falling):** When block N is empty and `P_{N+1} < P_N`, a transaction with `max_price_per_unit = P_{N+1}` fails the gateway check (`P_{N+1} < 100% × P_N`) even though it would be fully valid for block N+1. The user receives a spurious `GAS_PRICE_TOO_LOW` rejection.

Both outcomes match: **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

### Likelihood Explanation

The EIP-1559 gas price adjusts every block based on gas usage. Any block that is not exactly at the gas target causes `P_{N+1} ≠ P_N`, which is the normal operating condition. The divergence is bounded per block but is systematic and continuous, not a rare edge case.

### Recommendation

Replace the use of `previous_block_l2_gas_price` with the deterministically computed next-block L2 gas price. The gateway already has access to the previous block's gas usage (via state-sync) and the EIP-1559 parameters (via `VersionedConstants`), so `calculate_next_base_gas_price` can be called directly to derive the correct admission threshold. The existing TODO comment at line 229 of `stateful_transaction_validator.rs` already identifies this fix.

### Proof of Concept

**Scenario A — valid transaction incorrectly rejected:**

1. Block N is produced with zero gas usage; EIP-1559 decreases the price: `P_{N+1} = P_N × (1 − 1/D)` where `D = gas_price_max_change_denominator`.
2. User submits an invoke V3 transaction with `l2_gas.max_price_per_unit = P_{N+1}`.
3. Gateway calls `validate_resource_bounds`; threshold = `100% × P_N > P_{N+1}`.
4. Gateway returns `GAS_PRICE_TOO_LOW`; transaction is rejected.
5. The transaction would have been valid for block N+1 at price `P_{N+1}`.

**Scenario B — invalid transaction incorrectly admitted:**

1. Block N is produced at full capacity; EIP-1559 increases the price: `P_{N+1} = P_N × (1 + 1/D)`.
2. User submits an invoke V3 transaction with `l2_gas.max_price_per_unit = P_N`.
3. Gateway calls `validate_resource_bounds`; threshold = `100% × P_N = P_N`; check passes.
4. Transaction enters the mempool.
5. Block N+1 is built with gas price `P_{N+1} > P_N`; the transaction's `max_price_per_unit` is below the block price and the transaction is not included.
6. Transaction remains in the mempool consuming space until it expires.

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L229-240)
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

**File:** crates/apollo_versioned_constants/src/lib.rs (L13-15)
```rust
    /// between consecutive blocks.
    pub gas_price_max_change_denominator: u128,
    /// The minimum gas price in fri.
```

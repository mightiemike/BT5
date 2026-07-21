### Title
Stale Pending-Block Fallback Omits `l1_data_gas_price` and `l2_gas_price`, Causing Wrong Fee Estimates for `block_id = "pending"` - (File: `crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

---

### Summary

When the in-memory pending data is stale (its `parent_block_hash` does not match the latest committed block hash), `read_pending_data` constructs a fallback `DeprecatedPendingBlock`. This deprecated type structurally cannot carry `l1_data_gas_price` or `l2_gas_price`. Its accessor methods return `GasPricePerToken::default()` (zero) for both fields. Any RPC call that requests fee estimation, simulation, or tracing against `block_id = "pending"` during this window will execute with `NonzeroGasPrice::MIN` for data-gas and L2-gas prices instead of the real values from the latest committed block, producing authoritative-looking but wrong fee estimates.

---

### Finding Description

`read_pending_data` in `crates/apollo_rpc/src/v0_8/api/api_impl.rs` has two branches:

1. **Happy path** (line 1570): the cached pending block's `parent_block_hash` matches the latest committed block hash → return the real pending data as-is.
2. **Fallback** (lines 1572–1594): the cached pending block is stale → synthesize a `PendingBlockOrDeprecated::Deprecated(DeprecatedPendingBlock { … })`. [1](#0-0) 

The fallback copies only `eth_l1_gas_price` and `strk_l1_gas_price` from the latest block header. `DeprecatedPendingBlock` has no fields for `l1_data_gas_price` or `l2_gas_price`. [2](#0-1) 

The accessor methods `l1_data_gas_price()` and `l2_gas_price()` on `PendingBlockOrDeprecated::Deprecated(_)` unconditionally return `GasPricePerToken::default()` (both `price_in_wei` and `price_in_fri` are zero). This is correct for genuinely old blocks, but the fallback path uses this type for *current-era* blocks.

`client_pending_data_to_execution_pending_data` in `crates/apollo_rpc/src/pending.rs` faithfully forwards these zero values into `ExecutionPendingData.l1_data_gas_price` and `ExecutionPendingData.l2_gas_price`. [3](#0-2) 

`create_block_context_for_execution` in `crates/apollo_rpc_execution/src/lib.rs` then calls `NonzeroGasPrice::new(zero).unwrap_or(NonzeroGasPrice::MIN)` for both fields, silently substituting the minimum representable price. [4](#0-3) 

This block context is used directly by `estimate_fee`, `simulate_transactions`, and `estimate_message_fee` when `block_id = Tag::Pending`. [5](#0-4) 

---

### Impact Explanation

The `l1_data_gas_price` and `l2_gas_price` fields are direct inputs to `tx_execution_output_to_fee_estimation`, which computes `l1_data_gas_price`, `l2_gas_price`, and `overall_fee` returned to the caller. [6](#0-5) 

When the fallback fires, both prices are `NonzeroGasPrice::MIN` (effectively 1 wei / 1 fri) instead of the real values from the latest committed block. For any transaction that consumes data gas or L2 gas, the returned `overall_fee`, `l1_data_gas_price`, and `l2_gas_price` fields are wrong. A client relying on `starknet_estimateFee` with `block_id = "pending"` will receive a severely underestimated fee, potentially causing transactions to be submitted with insufficient resource bounds and fail on-chain.

This matches the allowed impact: **High — RPC fee estimation returns an authoritative-looking wrong value.**

---

### Likelihood Explanation

The fallback is triggered whenever the in-memory pending data has not yet been refreshed after a new block is committed. This is a normal operational condition that occurs:

- At node startup, before the first pending block is received from the feeder gateway.
- During any gap in the pending-data polling loop.
- During sync, when the node is catching up and the pending cache lags behind committed state.

No privilege is required; any caller of `starknet_estimateFee`, `starknet_simulateTransactions`, or `starknet_estimateMessageFee` with `block_id = "pending"` can observe the wrong values during these windows.

---

### Recommendation

Replace the `DeprecatedPendingBlock` fallback with a `PendingBlock` (the `Current` variant) that copies all three gas-price fields from the latest committed block header:

```rust
// In read_pending_data, stale-pending fallback branch:
block: PendingBlockOrDeprecated::Current(PendingBlock {
    parent_block_hash: latest_header.block_hash,
    l1_gas_price: latest_header.block_header_without_hash.l1_gas_price,
    l1_data_gas_price: latest_header.block_header_without_hash.l1_data_gas_price,
    l2_gas_price: latest_header.block_header_without_hash.l2_gas_price,
    l1_da_mode: latest_header.block_header_without_hash.l1_da_mode,
    timestamp: latest_header.block_header_without_hash.timestamp,
    sequencer_address: latest_header.block_header_without_hash.sequencer,
    starknet_version: latest_header.block_header_without_hash.starknet_version.to_string(),
    ..Default::default()
}),
```

This ensures that fee estimation against the pending block always uses the real gas prices from the latest committed block as the baseline, matching the behavior of the happy-path branch.

---

### Proof of Concept

1. Start a node. Before the first pending-data update arrives (or simulate a stale cache by setting `pending_data.block.parent_block_hash` to a value that does not match the latest committed block hash).
2. Call `starknet_estimateFee` with any V3 transaction (which charges L2 gas) and `block_id = "pending"`.
3. Observe that the returned `l2_gas_price` equals `NonzeroGasPrice::MIN` (1 fri) rather than the real L2 gas price from the latest block header.
4. The `overall_fee` will be computed using this artificially low price, producing a value far below the actual cost of execution.

The divergence is deterministic: `DeprecatedPendingBlock::l2_gas_price()` always returns `GasPricePerToken { price_in_wei: GasPrice(0), price_in_fri: GasPrice(0) }`, and `NonzeroGasPrice::new(GasPrice(0))` always falls back to `NonzeroGasPrice::MIN`. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1009-1016)
```rust
        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
            None
        };
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1572-1594)
```rust
    } else {
        Ok(PendingData {
            block: PendingBlockOrDeprecated::Deprecated(DeprecatedPendingBlock {
                parent_block_hash: latest_header.block_hash,
                eth_l1_gas_price: latest_header.block_header_without_hash.l1_gas_price.price_in_wei,
                strk_l1_gas_price: latest_header
                    .block_header_without_hash
                    .l1_gas_price
                    .price_in_fri,
                timestamp: latest_header.block_header_without_hash.timestamp,
                sequencer_address: latest_header.block_header_without_hash.sequencer,
                starknet_version: latest_header
                    .block_header_without_hash
                    .starknet_version
                    .to_string(),
                ..Default::default()
            }),
            state_update: ClientPendingStateUpdate {
                old_root: latest_header.block_header_without_hash.state_root,
                state_diff: Default::default(),
            },
        })
    }
```

**File:** crates/apollo_starknet_client/src/reader/objects/pending_data.rs (L155-168)
```rust
    pub fn l1_data_gas_price(&self) -> GasPricePerToken {
        match self {
            // In older versions, data gas price was 0.
            PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(),
            PendingBlockOrDeprecated::Current(block) => block.l1_data_gas_price,
        }
    }
    pub fn l2_gas_price(&self) -> GasPricePerToken {
        match self {
            // In older versions, L2 gas price was 0.
            PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(),
            PendingBlockOrDeprecated::Current(block) => block.l2_gas_price,
        }
    }
```

**File:** crates/apollo_rpc/src/pending.rs (L5-24)
```rust
pub(crate) fn client_pending_data_to_execution_pending_data(
    client_pending_data: ClientPendingData,
    pending_classes: PendingClasses,
) -> ExecutionPendingData {
    ExecutionPendingData {
        storage_diffs: client_pending_data.state_update.state_diff.storage_diffs,
        deployed_contracts: client_pending_data.state_update.state_diff.deployed_contracts,
        declared_classes: client_pending_data.state_update.state_diff.declared_classes,
        old_declared_contracts: client_pending_data.state_update.state_diff.old_declared_contracts,
        nonces: client_pending_data.state_update.state_diff.nonces,
        replaced_classes: client_pending_data.state_update.state_diff.replaced_classes,
        classes: pending_classes,
        timestamp: client_pending_data.block.timestamp(),
        l1_gas_price: client_pending_data.block.l1_gas_price(),
        l1_data_gas_price: client_pending_data.block.l1_data_gas_price(),
        l2_gas_price: client_pending_data.block.l2_gas_price(),
        l1_da_mode: client_pending_data.block.l1_da_mode(),
        sequencer: client_pending_data.block.sequencer_address(),
    }
}
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L384-395)
```rust
                l1_data_gas_price: NonzeroGasPrice::new(l1_data_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
            },
            strk_gas_prices: GasPriceVector {
                l1_gas_price: NonzeroGasPrice::new(l1_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l1_data_gas_price: NonzeroGasPrice::new(l1_data_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
```

**File:** crates/apollo_rpc_execution/src/objects.rs (L161-183)
```rust
pub(crate) fn tx_execution_output_to_fee_estimation(
    tx_execution_output: &TransactionExecutionOutput,
    block_context: &BlockContext,
) -> ExecutionResult<FeeEstimation> {
    let gas_prices = &block_context.block_info().gas_prices;
    let (l1_gas_price, l1_data_gas_price, l2_gas_price) = (
        gas_prices.l1_gas_price(&tx_execution_output.price_unit.into()).get(),
        gas_prices.l1_data_gas_price(&tx_execution_output.price_unit.into()).get(),
        gas_prices.l2_gas_price(&tx_execution_output.price_unit.into()).get(),
    );

    let gas_vector = tx_execution_output.execution_info.receipt.gas;

    Ok(FeeEstimation {
        gas_consumed: gas_vector.l1_gas.0.into(),
        l1_gas_price,
        data_gas_consumed: gas_vector.l1_data_gas.0.into(),
        l1_data_gas_price,
        l2_gas_price,
        overall_fee: tx_execution_output.execution_info.receipt.fee,
        unit: tx_execution_output.price_unit,
    })
}
```

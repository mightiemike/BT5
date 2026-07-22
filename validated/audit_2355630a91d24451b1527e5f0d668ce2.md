### Title
`FeeEstimation` omits `l2_gas_consumed` while `overall_fee` includes L2 gas cost, making the fee breakdown formula wrong — (`crates/apollo_rpc_execution/src/objects.rs`)

---

### Summary

`starknet_estimateFee` and `starknet_simulateTransactions` return a `FeeEstimation` object whose `overall_fee` silently includes L2 gas costs, but the response exposes no `l2_gas_consumed` field. The struct's own documentation claims `overall_fee = gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price`, which is false for any V3 transaction that consumes L2 gas. Any client that reconstructs the fee from the breakdown fields will compute a value lower than `overall_fee`, receiving an authoritative-looking wrong value from the RPC.

---

### Finding Description

`FeeEstimation` in `crates/apollo_rpc_execution/src/objects.rs` is defined as:

```rust
pub struct FeeEstimation {
    pub gas_consumed: Felt,        // L1 gas only
    pub l1_gas_price: GasPrice,
    pub data_gas_consumed: Felt,   // L1 data gas only
    pub l1_data_gas_price: GasPrice,
    // TODO(Tzahi): Add l2_gas_consumed. ...
    pub l2_gas_price: GasPrice,    // price present, but no consumed amount
    /// The total amount of fee. This is equal to:
    /// gas_consumed * gas_price + data_gas_consumed * data_gas_price.
    pub overall_fee: Fee,
    pub unit: PriceUnit,
}
``` [1](#0-0) 

The conversion function `tx_execution_output_to_fee_estimation` populates `overall_fee` directly from `receipt.fee`, which is the actual charged fee computed by the blockifier and includes all three gas components (L1, L1 data, and L2):

```rust
Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(),
    l1_data_gas_price,
    l2_gas_price,
    overall_fee: tx_execution_output.execution_info.receipt.fee,  // includes L2 gas cost
    unit: tx_execution_output.price_unit,
})
``` [2](#0-1) 

`gas_vector.l2_gas` is never placed into the response. The `overall_fee` is the full fee including L2 gas, but `l2_gas_consumed` is absent. The documented formula is therefore wrong for any V3 (`AllResources`) transaction.

The discrepancy is confirmed by the `starknet_transaction_prover` test fixture, which records the correct full breakdown from the upstream node — including `l2_gas_consumed` — while the Apollo RPC response struct cannot represent it:

```json
"fee_estimation": {
  "l1_gas_consumed": "0x0",
  "l1_data_gas_consumed": "0x80",
  "l2_gas_consumed": "0xb56b6",
  "l2_gas_price": "0x1dcd65000",
  "overall_fee": "0x151eb86f3ed400"
}
``` [3](#0-2) 

The `overall_fee` in that fixture is `0x151eb86f3ed400`. The L2 gas contribution alone is `0xb56b6 * 0x1dcd65000 = 0x151eb86f3c0000`, which is the dominant term. The formula `gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price` yields only `0 + 128 * 1000 = 128000`, which is orders of magnitude smaller than `overall_fee`.

---

### Impact Explanation

This matches the allowed impact: **"High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."**

Any client that:
1. Calls `starknet_estimateFee` or `starknet_simulateTransactions` on a V3 transaction
2. Reconstructs the fee from the returned breakdown fields using the documented formula
3. Uses that reconstructed value to set `resource_bounds` for the actual submission

will set `resource_bounds` far below the actual fee, causing the transaction to revert post-execution with `Insufficient max L2Gas`. The `overall_fee` field is the only correct value in the response, but it cannot be decomposed into per-resource amounts from the fields provided, making the response structurally misleading.

---

### Likelihood Explanation

All V3 transactions with non-zero L2 gas consumption (i.e., all modern Starknet transactions using `AllResources` bounds) are affected. Any wallet, SDK, or dApp that calls `starknet_estimateFee` and uses the breakdown formula to set `l2_gas.max_amount` will receive a wrong value. The trigger is unprivileged: any user submitting a V3 transaction through the RPC.

---

### Recommendation

Add `l2_gas_consumed` to `FeeEstimation` and populate it in `tx_execution_output_to_fee_estimation`:

```rust
pub struct FeeEstimation {
    pub gas_consumed: Felt,
    pub l1_gas_price: GasPrice,
    pub data_gas_consumed: Felt,
    pub l1_data_gas_price: GasPrice,
    pub l2_gas_consumed: Felt,          // add this
    pub l2_gas_price: GasPrice,
    pub overall_fee: Fee,
    pub unit: PriceUnit,
}
```

And in `tx_execution_output_to_fee_estimation`:

```rust
Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(),
    l1_data_gas_price,
    l2_gas_consumed: gas_vector.l2_gas.0.into(),   // add this
    l2_gas_price,
    overall_fee: tx_execution_output.execution_info.receipt.fee,
    unit: tx_execution_output.price_unit,
})
```

Also correct the doc comment on `overall_fee` to include the L2 gas term.

---

### Proof of Concept

1. Submit a V3 invoke transaction to `starknet_estimateFee`.
2. Observe the response: `l2_gas_price` is non-zero, `overall_fee` is non-zero, but no `l2_gas_consumed` field is present.
3. Compute `reconstructed_fee = gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price`.
4. Observe `reconstructed_fee << overall_fee` (the difference is `l2_gas_consumed * l2_gas_price`).
5. Set `l2_gas.max_amount = reconstructed_fee / l2_gas_price` and submit the transaction.
6. The transaction reverts with `Insufficient max L2Gas` because the actual L2 gas used exceeds the bound derived from the broken formula.

The existing TODO comment at line 104 of `objects.rs` acknowledges the missing field:

```
// TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
// close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
``` [4](#0-3)

### Citations

**File:** crates/apollo_rpc_execution/src/objects.rs (L94-113)
```rust
#[derive(Debug, Serialize, Deserialize, PartialEq, Eq, Clone)]
pub struct FeeEstimation {
    /// Gas consumed by this transaction. This includes gas for DA in calldata mode.
    pub gas_consumed: Felt,
    /// The gas price for execution and calldata DA.
    pub l1_gas_price: GasPrice,
    /// Gas consumed by DA in blob mode.
    pub data_gas_consumed: Felt,
    /// The gas price for DA blob.
    pub l1_data_gas_price: GasPrice,
    // TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
    // close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
    /// The L2 gas price for execution.
    pub l2_gas_price: GasPrice,
    /// The total amount of fee. This is equal to:
    /// gas_consumed * gas_price + data_gas_consumed * data_gas_price.
    pub overall_fee: Fee,
    /// The unit in which the fee was paid (Wei/Fri).
    pub unit: PriceUnit,
}
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

**File:** crates/starknet_transaction_prover/resources/rpc_records/test_simulate_and_get_initial_reads.json (L91-101)
```json
            {
              "fee_estimation": {
                "l1_data_gas_consumed": "0x80",
                "l1_data_gas_price": "0x3e8",
                "l1_gas_consumed": "0x0",
                "l1_gas_price": "0xe8d4a51000",
                "l2_gas_consumed": "0xb56b6",
                "l2_gas_price": "0x1dcd65000",
                "overall_fee": "0x151eb86f3ed400",
                "unit": "FRI"
              },
```

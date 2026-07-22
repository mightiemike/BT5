### Title
`FeeEstimation` Omits `l2_gas_consumed` While `overall_fee` Silently Includes L2 Gas Cost — (`crates/apollo_rpc_execution/src/objects.rs`)

### Summary

The `FeeEstimation` struct returned by the sequencer's RPC fee-estimation path exposes `l2_gas_price` but never exposes `l2_gas_consumed`. The `overall_fee` field is populated from the execution receipt and therefore **does** include the L2 gas component, yet the OpenRPC v0.8 schema describes `overall_fee` as equalling only `gas_consumed * gas_price + data_gas_consumed * data_gas_price`. Any client that reconstructs the fee from the disclosed breakdown fields will compute a value strictly lower than `overall_fee` for every transaction that consumes non-zero L2 gas, making the response an authoritative-looking wrong value.

### Finding Description

`tx_execution_output_to_fee_estimation` in `crates/apollo_rpc_execution/src/objects.rs` builds the fee-estimation response:

```rust
Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(),
    l1_data_gas_price,
    l2_gas_price,                                          // price exposed …
    overall_fee: tx_execution_output.execution_info.receipt.fee,  // includes l2 cost
    unit: tx_execution_output.price_unit,
})
``` [1](#0-0) 

`gas_vector.l2_gas` is never written into the response. The `overall_fee` is taken directly from `receipt.fee`, which is computed by `get_fee_by_gas_vector` over the full `GasVector { l1_gas, l1_data_gas, l2_gas }`: [2](#0-1) 

The `FeeEstimation` struct itself carries an explicit TODO acknowledging the gap:

```rust
// TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
// close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
pub l2_gas_price: GasPrice,
``` [3](#0-2) 

The OpenRPC v0.8 schema reinforces the broken invariant by describing `overall_fee` as:

> "equals to gas_consumed\*gas_price + data_gas_consumed\*data_gas_price" [4](#0-3) 

The exact divergent value for any transaction consuming `G_l2` units of L2 gas at price `P_l2` is:

```
overall_fee − (gas_consumed × l1_gas_price + data_gas_consumed × l1_data_gas_price)
  = G_l2 × P_l2   (non-zero for all post-0.13.3 V3 transactions)
```

### Impact Explanation

Matches **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

A client that calls `starknet_estimateFee`, reads the documented formula, and computes `gas_consumed × l1_gas_price + data_gas_consumed × l1_data_gas_price` will obtain a value that is strictly less than `overall_fee`. The response is self-contradictory: it supplies `l2_gas_price` (implying L2 gas matters) but omits `l2_gas_consumed` (preventing reconstruction), while the schema asserts the formula is complete. Wallets and SDKs that verify the fee or use the breakdown to set resource bounds will either reject valid fee estimates or construct under-funded transactions.

### Likelihood Explanation

Every V3 transaction on a post-0.13.3 network consumes non-zero L2 gas. The endpoint is publicly reachable with no privilege requirement. The discrepancy is present in every fee-estimation call.

### Recommendation

1. Add `l2_gas_consumed: Felt` to `FeeEstimation` and populate it from `gas_vector.l2_gas.0.into()` in `tx_execution_output_to_fee_estimation`.
2. Update the OpenRPC schema description of `overall_fee` to include the L2 gas term.
3. Add `l2_gas_consumed` to the `required` array in the `FEE_ESTIMATE` schema object.

### Proof of Concept

Submit any V3 invoke transaction to `starknet_estimateFee` on a node running this code. Observe:

```
overall_fee  >  gas_consumed × l1_gas_price + data_gas_consumed × l1_data_gas_price
```

The difference equals `l2_gas_consumed × l2_gas_price`. Because `l2_gas_consumed` is absent from the response, the client cannot account for this component, and the schema-documented formula produces a provably wrong (lower) value than the authoritative `overall_fee` field.

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

**File:** crates/blockifier/src/fee/receipt.rs (L105-119)
```rust
        let gas = tx_resources.to_gas_vector(
            &tx_context.block_context.versioned_constants,
            tx_context.block_context.block_info.use_kzg_da,
            &gas_mode,
        );
        // Backward-compatibility.
        let fee = if tx_type == TransactionType::Declare && tx_context.tx_info.is_v0() {
            Fee(0)
        } else {
            tx_context.tx_info.get_fee_by_gas_vector(
                &tx_context.block_context.block_info,
                gas,
                tx_context.effective_tip(),
            )
        };
```

**File:** crates/apollo_rpc/resources/V0_8/starknet_api_openrpc.json (L3648-3666)
```json
                    "overall_fee": {
                        "title": "Overall fee",
                        "description": "The estimated fee for the transaction (in wei or fri, depending on the tx version), equals to gas_consumed*gas_price + data_gas_consumed*data_gas_price",
                        "$ref": "#/components/schemas/FELT"
                    },
                    "unit": {
                        "title": "Fee unit",
                        "description": "units in which the fee is given",
                        "$ref": "#/components/schemas/PRICE_UNIT"
                    }
                },
                "required": [
                    "gas_consumed",
                    "l1_gas_price",
                    "data_gas_consumed",
                    "l1_data_gas_price",
                    "overall_fee",
                    "unit"
                ]
```

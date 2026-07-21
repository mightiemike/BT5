### Title
`FeeEstimation` omits `l2_gas_consumed` while `overall_fee` includes L2 gas cost, producing an irreconcilable fee breakdown for v3 transactions - (File: crates/apollo_rpc_execution/src/objects.rs)

### Summary

`starknet_estimateFee` and `starknet_simulateTransactions` return a `FeeEstimation` object whose `overall_fee` correctly includes L2 gas costs, but whose individual gas-consumption fields omit `l2_gas_consumed`. The OpenRPC spec states `overall_fee = gas_consumed * gas_price + data_gas_consumed * data_gas_price`, but for any v3 transaction with non-zero L2 gas usage this formula produces a value strictly less than `overall_fee`. The discrepancy equals `l2_gas_consumed * l2_gas_price`, which is silently absorbed into `overall_fee` with no corresponding field in the response.

### Finding Description

`tx_execution_output_to_fee_estimation` in `crates/apollo_rpc_execution/src/objects.rs` builds the `FeeEstimation` struct as follows:

```rust
Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),       // L1 gas only
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(), // L1 data gas only
    l1_data_gas_price,
    // TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of
    // l1_gas_price only is close enough (as there are roundings) to the fee
    // of both l1_gas_price and l2_gas_price.
    l2_gas_price,
    overall_fee: tx_execution_output.execution_info.receipt.fee,  // includes L2 gas
    unit: tx_execution_output.price_unit,
})
```

`gas_vector.l2_gas` is never placed into any response field. Meanwhile `receipt.fee` is computed by `GasVector::cost()` which sums all three resources:

```rust
for (gas, price, resource) in [
    (self.l1_gas, gas_prices.l1_gas_price, Resource::L1Gas),
    (self.l1_data_gas, gas_prices.l1_data_gas_price, Resource::L1DataGas),
    (self.l2_gas, tipped_l2_gas_price, Resource::L2Gas),   // included in fee
] { ... }
```

The `FeeEstimation` struct definition itself carries a TODO acknowledging the missing field:

```rust
// TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
// close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
pub l2_gas_price: GasPrice,
```

The OpenRPC schema at `crates/apollo_rpc/resources/V0_8/starknet_api_openrpc.json` describes `overall_fee` as:

> "equals to gas_consumed\*gas_price + data_gas_consumed\*data_gas_price"

For any v3 transaction (`AllResources` bounds) with non-zero L2 gas consumption, this formula yields a value lower than the actual `overall_fee` by exactly `l2_gas_consumed * l2_gas_price`. The L2 gas price is present in the response (`l2_gas_price`) but the consumed amount is absent, making it impossible for a client to reconstruct or verify the fee.

### Impact Explanation

This matches **High – RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

A client that calls `starknet_estimateFee` for a v3 transaction receives:
- `gas_consumed` (L1 only), `l1_gas_price`, `data_gas_consumed`, `l1_data_gas_price`, `l2_gas_price`, `overall_fee`

The client cannot reconcile `overall_fee` with the provided components using the documented formula. The `overall_fee` is the authoritative value used for fee charging, but the breakdown is wrong. Clients that:
1. Verify the fee by recomputing from components will see a mismatch and may incorrectly flag valid transactions as malformed.
2. Use `gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price` as their fee estimate (following the spec formula) will systematically underestimate the actual fee charged, potentially causing transactions to fail due to insufficient balance.
3. Build fee-estimation tooling or wallets on top of this RPC will display incorrect fee breakdowns to users.

### Likelihood Explanation

Every v3 transaction (`AllResources` bounds, Starknet ≥ 0.13.3) that executes Cairo code will consume non-zero L2 gas. The test fixture in `crates/apollo_rpc/src/v0_8/execution_test.rs` confirms `l2_gas_price` is non-zero in the expected fee estimate. Any unprivileged user submitting a v3 transaction via `starknet_estimateFee` triggers the discrepancy.

### Recommendation

1. Add `l2_gas_consumed: Felt` to the `FeeEstimation` struct in `crates/apollo_rpc_execution/src/objects.rs` and populate it from `gas_vector.l2_gas.0.into()` in `tx_execution_output_to_fee_estimation`.
2. Update the OpenRPC schema description of `overall_fee` in `crates/apollo_rpc/resources/V0_8/starknet_api_openrpc.json` to reflect the correct formula: `gas_consumed*l1_gas_price + data_gas_consumed*l1_data_gas_price + l2_gas_consumed*l2_gas_price`.
3. Add `l2_gas_consumed` to the `FEE_ESTIMATE` schema object in the OpenRPC spec.
4. Resolve the TODO comment at `crates/apollo_rpc_execution/src/objects.rs:104`.

### Proof of Concept

Given a v3 invoke transaction with `AllResources` bounds on a block where `l2_gas_price > 0`:

1. Call `starknet_estimateFee` → receive response with `gas_consumed = X`, `l1_gas_price = P1`, `data_gas_consumed = Y`, `l1_data_gas_price = P2`, `l2_gas_price = P3`, `overall_fee = F`.
2. Compute `F_reconstructed = X * P1 + Y * P2`.
3. Observe `F_reconstructed < F` by exactly `l2_gas_consumed * P3`.
4. The missing term `l2_gas_consumed` is `gas_vector.l2_gas` inside `tx_execution_output_to_fee_estimation` — it is computed but never returned.

The test constant `EXPECTED_FEE_ESTIMATE` in `crates/apollo_rpc/src/v0_8/execution_test.rs` already sets `l2_gas_price = L2_GAS_PRICE.price_in_wei` (non-zero), confirming the field is live, while `l2_gas_consumed` is absent from the struct, confirming the omission is present in the production code path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/starknet_api/src/execution_resources.rs (L155-186)
```rust
    /// Computes the cost (in fee token units) of the gas vector (panicking on overflow).
    pub fn cost(&self, gas_prices: &GasPriceVector, tip: Tip) -> Fee {
        let tipped_l2_gas_price =
            gas_prices.l2_gas_price.checked_add(tip.into()).unwrap_or_else(|| {
                panic!(
                    "Tip overflowed: addition of L2 gas price ({}) and tip ({}) resulted in \
                     overflow.",
                    gas_prices.l2_gas_price, tip
                )
            });

        let mut sum = Fee(0);
        for (gas, price, resource) in [
            (self.l1_gas, gas_prices.l1_gas_price, Resource::L1Gas),
            (self.l1_data_gas, gas_prices.l1_data_gas_price, Resource::L1DataGas),
            (self.l2_gas, tipped_l2_gas_price, Resource::L2Gas),
        ] {
            let cost = gas.checked_mul(price.get()).unwrap_or_else(|| {
                panic!(
                    "{resource} cost overflowed: multiplication of gas amount ({gas}) by price \
                     per unit ({price}) resulted in overflow."
                )
            });
            sum = sum.checked_add(cost).unwrap_or_else(|| {
                panic!(
                    "Total cost overflowed: addition of current sum ({sum}) and cost of \
                     {resource} ({cost}) resulted in overflow."
                )
            });
        }
        sum
    }
```

**File:** crates/apollo_rpc/resources/V0_8/starknet_api_openrpc.json (L3624-3660)
```json
            "FEE_ESTIMATE": {
                "title": "Fee estimation",
                "type": "object",
                "properties": {
                    "gas_consumed": {
                        "title": "Gas consumed",
                        "description": "The Ethereum gas consumption of the transaction",
                        "$ref": "#/components/schemas/FELT"
                    },
                    "l1_gas_price": {
                        "title": "Gas price",
                        "description": "The gas price (in wei or fri, depending on the tx version) that was used in the cost estimation",
                        "$ref": "#/components/schemas/FELT"
                    },
                    "data_gas_consumed": {
                        "title": "Data gas consumed",
                        "description": "The Ethereum data gas consumption of the transaction",
                        "$ref": "#/components/schemas/FELT"
                    },
                    "l1_data_gas_price": {
                        "title": "Data gas price",
                        "description": "The data gas price (in wei or fri, depending on the tx version) that was used in the cost estimation",
                        "$ref": "#/components/schemas/FELT"
                    },
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
```

**File:** crates/apollo_rpc/src/v0_8/execution_test.rs (L175-183)
```rust
    pub static ref EXPECTED_FEE_ESTIMATE: FeeEstimation = FeeEstimation {
        gas_consumed: felt!("0x683"),
        l1_gas_price: GAS_PRICE.price_in_wei,
        data_gas_consumed: Felt::ZERO,
        l1_data_gas_price: DATA_GAS_PRICE.price_in_wei,
        l2_gas_price: L2_GAS_PRICE.price_in_wei,
        overall_fee: Fee(166700000000000,),
        unit: PriceUnit::Wei,
    };
```

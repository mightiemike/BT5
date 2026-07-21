### Title
`FeeEstimation` omits `l2_gas_consumed`, making `overall_fee` irreconcilable with reported gas components - (`File: crates/apollo_rpc_execution/src/objects.rs`)

### Summary

`starknet_estimateFee` and `starknet_simulateTransactions` return a `FeeEstimation` object whose `overall_fee` silently includes an L2 gas cost component that is never reported to the caller. The struct comment and the RPC OpenAPI description both assert `overall_fee = gas_consumed * gas_price + data_gas_consumed * data_gas_price`, but for every V3 (`AllResources`) transaction the actual fee is `l1_gas * l1_gas_price + l1_data_gas * l1_data_gas_price + l2_gas * l2_gas_price`. The L2 gas consumed is never surfaced, so the two sides of the equation never balance.

### Finding Description

`tx_execution_output_to_fee_estimation` in `crates/apollo_rpc_execution/src/objects.rs` builds the `FeeEstimation` response from the execution receipt:

```rust
let gas_vector = tx_execution_output.execution_info.receipt.gas;

Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),       // L1 gas only
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(), // L1 data gas only
    l1_data_gas_price,
    l2_gas_price,                                   // price reported …
    overall_fee: tx_execution_output.execution_info.receipt.fee, // … but l2_gas_consumed is NOT
    unit: tx_execution_output.price_unit,
})
``` [1](#0-0) 

`gas_vector.l2_gas` is read from the receipt but never placed into the response. The struct's own doc comment codifies the wrong invariant:

```rust
/// The total amount of fee. This is equal to:
/// gas_consumed * gas_price + data_gas_consumed * data_gas_price.
pub overall_fee: Fee,
``` [2](#0-1) 

An internal TODO in the same struct acknowledges the gap:

```rust
// TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
// close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
``` [3](#0-2) 

The OpenAPI schema for `FEE_ESTIMATE` repeats the same false description:

```json
"overall_fee": {
    "description": "…equals to gas_consumed*gas_price + data_gas_consumed*data_gas_price"
``` [4](#0-3) 

The actual fee is computed by `GasVector::cost`, which sums all three gas dimensions including L2 gas multiplied by `l2_gas_price` (plus tip). For any V3 transaction with non-zero L2 gas usage the reported components sum to strictly less than `overall_fee`.

### Impact Explanation

Any caller consuming `starknet_estimateFee` or `starknet_simulateTransactions` to construct a V3 transaction's resource bounds receives an authoritative-looking but internally inconsistent response:

1. `overall_fee` is the true fee (correct).
2. `gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price` is less than `overall_fee` by exactly `l2_gas * l2_gas_price`.
3. `l2_gas_consumed` is absent, so the caller cannot derive the correct `l2_gas.max_amount` to set.

A wallet or SDK that follows the documented formula to reconstruct resource bounds will compute `l2_gas_consumed = 0`. Setting `l2_gas.max_amount` to zero or to an under-estimated value causes the transaction to fail pre-validation (`MaxGasAmountExceeded` for L2 gas) or to be rejected at the gateway. The RPC endpoint returns an authoritative-looking wrong value, matching the **High** impact scope: *"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

### Likelihood Explanation

Every V3 (`AllResources`) transaction consumes L2 gas. The gateway only accepts V3 transactions (version 3 is the only supported version). Therefore every fee estimation call for a current transaction triggers this inconsistency. No special attacker capability is required; any user calling `starknet_estimateFee` observes the divergence.

### Recommendation

Add `l2_gas_consumed` to `FeeEstimation` and populate it from `gas_vector.l2_gas`:

```rust
pub struct FeeEstimation {
    pub gas_consumed: Felt,
    pub l1_gas_price: GasPrice,
    pub data_gas_consumed: Felt,
    pub l1_data_gas_price: GasPrice,
+   pub l2_gas_consumed: Felt,   // add this field
    pub l2_gas_price: GasPrice,
    /// overall_fee = gas_consumed*l1_gas_price
    ///             + data_gas_consumed*l1_data_gas_price
    ///             + l2_gas_consumed*l2_gas_price
    pub overall_fee: Fee,
    pub unit: PriceUnit,
}
```

In `tx_execution_output_to_fee_estimation`:

```rust
Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(),
    l1_data_gas_price,
+   l2_gas_consumed: gas_vector.l2_gas.0.into(),
    l2_gas_price,
    overall_fee: ...,
    unit: ...,
})
```

Update the OpenAPI `FEE_ESTIMATE` description and the struct doc comment to reflect the three-term formula.

### Proof of Concept

1. Call `starknet_estimateFee` for any V3 invoke transaction on a node running this code.
2. Receive a `FeeEstimation` with non-zero `overall_fee`, non-zero `l2_gas_price`, and some `gas_consumed` / `data_gas_consumed`.
3. Compute `reconstructed = gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price`.
4. Observe `reconstructed < overall_fee`; the gap equals `l2_gas * l2_gas_price` which is never disclosed.
5. A wallet that uses `reconstructed / l2_gas_price` to derive `l2_gas.max_amount` obtains zero, causing the subsequent transaction submission to fail with an L2 gas bound error.

The divergence is confirmed by the existing TODO comment at [3](#0-2)  and by the `FeeEstimation` struct definition at [5](#0-4) , which exposes `l2_gas_price` but provides no corresponding consumed-amount field.

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

**File:** crates/apollo_rpc_execution/src/objects.rs (L172-182)
```rust
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
```

**File:** crates/apollo_rpc/resources/V0_8/starknet_api_openrpc.json (L3648-3652)
```json
                    "overall_fee": {
                        "title": "Overall fee",
                        "description": "The estimated fee for the transaction (in wei or fri, depending on the tx version), equals to gas_consumed*gas_price + data_gas_consumed*data_gas_price",
                        "$ref": "#/components/schemas/FELT"
                    },
```

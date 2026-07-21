### Title
`FeeEstimation.overall_fee` Includes L2 Gas Cost But RPC Response Omits `l2_gas_consumed`, Causing Authoritative-Looking Wrong Fee Decomposition — (`File: crates/apollo_rpc_execution/src/objects.rs`)

### Summary

The `FeeEstimation` struct returned by `starknet_estimateFee` and `starknet_simulateTransactions` reports an `overall_fee` that is computed from all three gas components (L1 gas, L1 data gas, and L2 gas), but the serialized RPC response omits `l2_gas_consumed`. The documented formula in the struct and in the OpenRPC spec states `overall_fee = gas_consumed * gas_price + data_gas_consumed * data_gas_price`, which is arithmetically wrong for any V3 transaction with non-zero L2 gas usage. A caller who trusts the decomposition fields to reconstruct the fee will compute a value lower than `overall_fee`, with no way to account for the discrepancy.

### Finding Description

In `crates/apollo_rpc_execution/src/objects.rs`, the `FeeEstimation` struct is defined as:

```rust
pub struct FeeEstimation {
    pub gas_consumed: Felt,       // l1_gas
    pub l1_gas_price: GasPrice,
    pub data_gas_consumed: Felt,  // l1_data_gas
    pub l1_data_gas_price: GasPrice,
    // TODO(Tzahi): Add l2_gas_consumed. Verify overall_fee estimation of l1_gas_price only is
    // close enough (as there are roundings) to the fee of both l1_gas_price and l2_gas_price.
    pub l2_gas_price: GasPrice,
    pub overall_fee: Fee,
    pub unit: PriceUnit,
}
``` [1](#0-0) 

The `overall_fee` field is populated in `tx_execution_output_to_fee_estimation` directly from `tx_execution_output.execution_info.receipt.fee`, which is computed by `get_fee_by_gas_vector` over the full `GasVector { l1_gas, l1_data_gas, l2_gas }`:

```rust
Ok(FeeEstimation {
    gas_consumed: gas_vector.l1_gas.0.into(),
    l1_gas_price,
    data_gas_consumed: gas_vector.l1_data_gas.0.into(),
    l1_data_gas_price,
    l2_gas_price,
    overall_fee: tx_execution_output.execution_info.receipt.fee,  // includes l2_gas cost
    unit: tx_execution_output.price_unit,
})
``` [2](#0-1) 

The `receipt.fee` is computed via `GasVector::cost()`, which sums all three components:

```rust
for (gas, price, resource) in [
    (self.l1_gas, gas_prices.l1_gas_price, Resource::L1Gas),
    (self.l1_data_gas, gas_prices.l1_data_gas_price, Resource::L1DataGas),
    (self.l2_gas, tipped_l2_gas_price, Resource::L2Gas),
] { ... }
``` [3](#0-2) 

However, `l2_gas_consumed` is never populated in the `FeeEstimation` struct (the TODO comment acknowledges this). The OpenRPC spec for `FEE_ESTIMATE` also omits `l2_gas_consumed` from both the schema and the `required` array, and its `overall_fee` description still states the old two-term formula:

> "equals to gas_consumed\*gas_price + data_gas_consumed\*data_gas_price" [4](#0-3) 

The divergence is confirmed by the transaction prover's own RPC test records, which show `l2_gas_consumed` as a non-zero value (`"0xb56b6"`) alongside `overall_fee`, while the standard `FeeEstimation` struct has no such field: [5](#0-4) 

### Impact Explanation

**Impact: High — RPC execution/fee estimation returns an authoritative-looking wrong value.**

For any V3 transaction with non-zero L2 gas usage (which is the dominant transaction type post-0.13.3), the `starknet_estimateFee` and `starknet_simulateTransactions` responses return:
- A correct `overall_fee` (includes L2 gas cost)
- A `gas_consumed` (L1 gas only) and `data_gas_consumed` (L1 data gas only) that together do **not** reconstruct `overall_fee`

A client that follows the documented formula `overall_fee = gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price` will compute a value strictly less than the actual `overall_fee`. The gap equals `l2_gas_consumed * l2_gas_price`. For a typical transaction with `l2_gas_consumed = 0xb56b6` (≈ 742,070) at `l2_gas_price = 0x1dcd65000` (≈ 8 Gwei), the hidden L2 fee component is approximately `0.006 STRK` per transaction — a non-trivial amount that scales with contract complexity.

This is a serialization/RPC conversion boundary bug: the blockifier correctly computes and charges the full fee, but the RPC layer omits the L2 gas consumed field, making the response internally inconsistent. Wallets and SDKs that use the decomposition fields to validate or display fee breakdowns will show incorrect values.

### Likelihood Explanation

**High likelihood.** Every V3 transaction (post-0.13.3, `AllResources` bounds) with any Cairo execution produces non-zero `l2_gas`. The `GasVectorComputationMode::All` path is the default for all modern transactions. The bug is triggered by any call to `starknet_estimateFee` or `starknet_simulateTransactions` with a V3 transaction — no special conditions required.

### Recommendation

1. Add `l2_gas_consumed: Felt` to the `FeeEstimation` struct in `crates/apollo_rpc_execution/src/objects.rs` and populate it from `gas_vector.l2_gas.0.into()` in `tx_execution_output_to_fee_estimation`.
2. Update the OpenRPC schema in `crates/apollo_rpc/resources/V0_8/starknet_api_openrpc.json` to add `l2_gas_consumed` to `FEE_ESTIMATE` properties and `required`, and correct the `overall_fee` description to include the L2 gas term.
3. Update the `FeeEstimation` doc comment in `objects.rs` to reflect the correct three-term formula.

### Proof of Concept

The inconsistency is directly observable from the existing test fixture. In `crates/starknet_transaction_prover/resources/rpc_records/test_simulate_and_get_initial_reads.json`:

```json
"fee_estimation": {
  "l1_data_gas_consumed": "0x80",
  "l1_data_gas_price": "0x3e8",
  "l1_gas_consumed": "0x0",
  "l1_gas_price": "0xe8d4a51000",
  "l2_gas_consumed": "0xb56b6",
  "l2_gas_price": "0x1dcd65000",
  "overall_fee": "0x151eb86f3ed400",
  "unit": "FRI"
}
```

Verify the formula discrepancy:
- `gas_consumed * l1_gas_price` = `0x0 * 0xe8d4a51000` = `0`
- `data_gas_consumed * l1_data_gas_price` = `0x80 * 0x3e8` = `0x1f400`
- Sum per documented formula = `0x1f400`
- Actual `overall_fee` = `0x151eb86f3ed400`

The gap (`0x151eb86f3ed400 - 0x1f400 = 0x151eb86f1c9000`) equals `l2_gas_consumed * l2_gas_price` = `0xb56b6 * 0x1dcd65000` = `0x151eb86f1c9000`. The standard `FeeEstimation` struct returned by the sequencer's RPC omits `l2_gas_consumed`, so callers receive `overall_fee` with no way to verify or decompose it correctly. [6](#0-5) [7](#0-6)

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

**File:** crates/starknet_api/src/execution_resources.rs (L166-185)
```rust
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
```

**File:** crates/apollo_rpc/resources/V0_8/starknet_api_openrpc.json (L3624-3666)
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
                    "l1_gas_price",
                    "data_gas_consumed",
                    "l1_data_gas_price",
                    "overall_fee",
                    "unit"
                ]
```

**File:** crates/starknet_transaction_prover/resources/rpc_records/test_simulate_and_get_initial_reads.json (L92-101)
```json
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

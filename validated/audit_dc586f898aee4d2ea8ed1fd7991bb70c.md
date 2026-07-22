## Finding Analysis

Let me trace the full data flow to determine if the invariant violation is real and reachable.

**Step 1: `receipt.fee` computation in blockifier**

`TransactionReceipt::from_account_tx` (receipt.rs line 179) uses `tx_context.get_gas_vector_computation_mode()`. For a v3 `InvokeTransaction` with `AllResources` bounds (l2_gas > 0), this returns `GasVectorComputationMode::All`.

With `All` mode, `gas = tx_resources.to_gas_vector(...)` produces a `GasVector` with nonzero `l2_gas`. [1](#0-0) 

The fee is then computed via `get_fee_by_gas_vector` → `GasVector::cost()`, which explicitly sums all three components: [2](#0-1) 

So `receipt.fee = l1_gas * l1_gas_price + l1_data_gas * l1_data_gas_price + l2_gas * l2_gas_price`.

**Step 2: `tx_execution_output_to_fee_estimation` conversion** [3](#0-2) 

- `gas_consumed` ← `gas_vector.l1_gas` only
- `data_gas_consumed` ← `gas_vector.l1_data_gas` only
- `overall_fee` ← `receipt.fee` (which includes l2_gas cost)
- `l2_gas_consumed` — **absent, no field**

**Step 3: The `FeeEstimation` struct and its documented invariant** [4](#0-3) 

The doc comment states: *"The total amount of fee. This is equal to: gas_consumed * gas_price + data_gas_consumed * data_gas_price."* The TODO at line 104 explicitly acknowledges the missing field.

The RPC OpenAPI spec repeats this broken invariant: [5](#0-4) 

**Step 4: Public path**

Any unprivileged caller submitting a v3 `BroadcastedTransaction` with `AllResources` bounds (standard for Starknet ≥ 0.14.0) reaches `estimate_fee` → `exec_estimate_fee` → `tx_execution_output_to_fee_estimation`. [6](#0-5) 

---

### Title
`FeeEstimation.overall_fee` silently includes l2_gas cost while `l2_gas_consumed` is absent, violating the documented RPC invariant for v3 AllResources transactions — (`crates/apollo_rpc_execution/src/objects.rs`)

### Summary
For any v3 `InvokeTransaction` (or Declare/DeployAccount) with `AllResources` resource bounds (l2_gas > 0), `estimate_fee` and `simulate_transactions` return a `FeeEstimation` where `overall_fee` includes the l2_gas component of the fee, but `l2_gas_consumed` is not present in the serialized response. The documented formula `overall_fee = gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price` is violated, and the RPC OpenAPI spec repeats the same incorrect formula.

### Finding Description
`GasVector::cost()` sums all three gas components (l1_gas, l1_data_gas, l2_gas) when computing `receipt.fee`. [2](#0-1) 

`tx_execution_output_to_fee_estimation` copies `receipt.fee` verbatim into `overall_fee`, but only copies `l1_gas` → `gas_consumed` and `l1_data_gas` → `data_gas_consumed`. The `l2_gas` component of the gas vector is never exposed. [7](#0-6) 

The `FeeEstimation` struct has no `l2_gas_consumed` field (acknowledged by the TODO at line 104). [8](#0-7) 

The RPC spec's `FEE_ESTIMATE` schema describes `overall_fee` as `gas_consumed*gas_price + data_gas_consumed*data_gas_price`, which is incorrect for SierraGas-mode transactions. [5](#0-4) 

### Impact Explanation
Any client (wallet, dApp, SDK) that verifies or reconstructs the fee using the documented formula will compute a value lower than `overall_fee` whenever l2_gas > 0. The RPC response is authoritative-looking but internally inconsistent: `overall_fee` cannot be derived from the other fields in the response. This falls under **High — RPC fee estimation returns an authoritative-looking wrong value** (the documented formula is wrong, and the missing field makes the response unverifiable).

### Likelihood Explanation
This is triggered by any v3 transaction with `AllResources` bounds, which is the standard transaction format for Starknet ≥ 0.14.0. No special attacker capability is required — any user submitting a normal v3 transaction will produce this discrepancy.

### Recommendation
Add `l2_gas_consumed: Felt` to `FeeEstimation` (as the TODO at line 104 already notes), populate it from `gas_vector.l2_gas.0`, and update the doc comment and RPC OpenAPI schema to reflect the correct formula: `overall_fee = gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price + l2_gas_consumed * l2_gas_price`.

### Proof of Concept
1. Submit a v3 `InvokeTransaction` with `AllResources` bounds (nonzero `l2_gas` max_amount) to `starknet_estimateFee`.
2. Receive a `FeeEstimation` JSON response.
3. Compute `reconstructed = gas_consumed * l1_gas_price + data_gas_consumed * l1_data_gas_price`.
4. Assert `reconstructed == overall_fee` — this assertion **fails** whenever the transaction consumes nonzero l2_gas, because `overall_fee` also includes `l2_gas_consumed * l2_gas_price` but `l2_gas_consumed` is not in the response.

### Citations

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

**File:** crates/apollo_rpc/resources/V0_8/starknet_api_openrpc.json (L3648-3651)
```json
                    "overall_fee": {
                        "title": "Overall fee",
                        "description": "The estimated fee for the transaction (in wei or fri, depending on the tx version), equals to gas_consumed*gas_price + data_gas_consumed*data_gas_price",
                        "$ref": "#/components/schemas/FELT"
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L997-1063)
```rust
    #[instrument(skip(self, transactions), level = "debug", err, ret)]
    async fn estimate_fee(
        &self,
        transactions: Vec<BroadcastedTransaction>,
        simulation_flags: Vec<SimulationFlag>,
        block_id: BlockId,
    ) -> RpcResult<Vec<FeeEstimation>> {
        trace!("Estimating fee of transactions: {:#?}", transactions);
        let validate = !simulation_flags.contains(&SimulationFlag::SkipValidate);

        let storage_txn = self.storage_reader.begin_ro_txn().map_err(internal_server_error)?;

        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
            None
        };

        let executable_txns =
            transactions.into_iter().map(|tx| tx.try_into()).collect::<Result<_, _>>()?;

        let block_number = get_accepted_block_number(&storage_txn, block_id)?;
        let block_not_reverted_validator =
            BlockNotRevertedValidator::new(block_number, &storage_txn)?;
        drop(storage_txn);
        let state_number = StateNumber::unchecked_right_after_block(block_number);
        let execution_config = self.execution_config;

        let chain_id = self.chain_id.clone();
        let reader = self.storage_reader.clone();
        let class_manager_client =
            create_class_manager_client(self.class_manager_client.clone()).await;

        let estimate_fee_result = tokio::task::spawn_blocking(move || {
            exec_estimate_fee(
                executable_txns,
                &chain_id,
                reader,
                maybe_pending_data,
                state_number,
                block_number,
                &execution_config,
                validate,
                DONT_IGNORE_L1_DA_MODE,
                class_manager_client,
            )
        })
        .await
        .map_err(internal_server_error)?;

        block_not_reverted_validator.validate(&self.storage_reader)?;

        match estimate_fee_result {
            Ok(Ok(fees)) => Ok(fees),
            Ok(Err(reverted_tx)) => {
                Err(ErrorObjectOwned::from(JsonRpcError::<TransactionExecutionError>::from(
                    TransactionExecutionError {
                        transaction_index: reverted_tx.index,
                        execution_error: reverted_tx.revert_reason,
                    },
                )))
            }
            Err(err) => Err(internal_server_error(err)),
        }
```

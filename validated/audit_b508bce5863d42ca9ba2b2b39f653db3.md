### Title
Declare Transaction Minimal Gas Estimate Omits `n_compiled_class_hash_updates` DA Cost, Allowing Under-Resourced Transactions to Pass Pre-Validation - (File: `crates/blockifier/src/fee/gas_usage.rs`)

---

### Summary

`estimate_minimal_gas_vector` hard-codes `n_compiled_class_hash_updates: 0` for `Declare` transactions even though every V2/V3 Declare execution writes exactly one compiled class hash entry. The code itself carries an explicit acknowledgment: `// TODO(Yoni): BLOCKIFIER-RESET: should be 1.` This causes the pre-validation fee-bound check (`check_fee_bounds`) to accept Declare transactions whose declared resource bounds are too low to cover the actual DA cost, admitting them to the mempool and causing them to revert during block execution.

---

### Finding Description

In `estimate_minimal_gas_vector`, the `StateChangesCount` constructed for a `Declare` transaction is:

```rust
Transaction::Declare(_) => StateChangesCount {
    n_storage_updates: 1,
    n_class_hash_updates: 0,
    // TODO(Yoni): BLOCKIFIER-RESET: should be 1.
    n_compiled_class_hash_updates: 0,
    n_modified_contracts: 1,
},
``` [1](#0-0) 

The actual state changes produced by a V2/V3 Declare execution include `n_compiled_class_hash_updates: 1`, as confirmed by the test helper `declare_expected_state_changes_count`:

```rust
} else if version == TransactionVersion::TWO || version == TransactionVersion::THREE {
    StateChangesCount {
        n_storage_updates: 1,
        n_modified_contracts: 1,
        n_compiled_class_hash_updates: 1, // Also set compiled class hash.
        ..StateChangesCount::default()
    }
``` [2](#0-1) 

`get_onchain_data_segment_length` counts each compiled class hash update as **2 field elements** (`class_hash + compiled_class_hash`):

```rust
onchain_data_segment_length += state_changes_count.n_compiled_class_hash_updates * 2;
``` [3](#0-2) 

With KZG DA enabled, each field element costs `DATA_GAS_PER_FIELD_ELEMENT` L1 data gas. The missing 2 field elements translate to a concrete underestimate of `2 * DATA_GAS_PER_FIELD_ELEMENT` L1 data gas (or the equivalent SHARP gas in non-KZG mode).

`estimate_minimal_gas_vector` is called directly inside `check_fee_bounds`, which is the gate that decides whether a transaction's declared resource bounds are sufficient to proceed:

```rust
fn check_fee_bounds(&self, tx_context: &TransactionContext) -> TransactionPreValidationResult<()> {
    let minimal_gas_amount_vector = estimate_minimal_gas_vector(
        &tx_context.block_context,
        self,
        &tx_context.get_gas_vector_computation_mode(),
    );
    ...
    // For AllResources: checks minimal_gas_amount_vector.l1_data_gas <= l1_data_gas_resource_bounds.max_amount
``` [4](#0-3) 

`check_fee_bounds` is called from `perform_pre_validation_stage`, which runs both during gateway stateful validation and during batcher execution: [5](#0-4) 

The gateway's `run_validate_entry_point` creates an `AccountTransaction` and calls `blockifier_validator.validate(account_tx)`, which internally calls `perform_pre_validation_stage`: [6](#0-5) 

---

### Impact Explanation

An attacker submits a Declare V3 transaction with `l1_data_gas.max_amount` set to exactly the underestimated minimum (i.e., the value returned by `estimate_minimal_gas_vector` for the Declare, which omits the 2-field-element compiled class hash DA cost). The gateway's stateful validator calls `check_fee_bounds`, which compares the declared bound against the underestimated minimum and passes. The transaction is admitted to the mempool. When the batcher executes the transaction, the actual DA cost exceeds the declared `l1_data_gas.max_amount`, causing the transaction to revert. The user pays the fee for a reverted transaction, and the sequencer wastes execution resources on a transaction that was predictably going to fail.

This matches the **High** impact scope: *"Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."*

Additionally, `estimate_minimal_gas_vector` is used in RPC fee estimation contexts, so `starknet_estimateFee` for a Declare transaction will return an underestimated `l1_data_gas` value, matching the **High** impact: *"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

---

### Likelihood Explanation

The bug is present in all Declare V3 transactions on any network with KZG DA enabled (the production configuration). The trigger requires only a standard Declare V3 transaction with `l1_data_gas.max_amount` set to the value returned by `starknet_estimateFee` (which itself is underestimated). Any user following the RPC fee estimate will inadvertently craft such a transaction. No privileged access is required.

---

### Recommendation

Change `n_compiled_class_hash_updates` from `0` to `1` in the `Transaction::Declare` arm of `estimate_minimal_gas_vector`, as the TODO comment already acknowledges:

```rust
Transaction::Declare(_) => StateChangesCount {
    n_storage_updates: 1,
    n_class_hash_updates: 0,
    n_compiled_class_hash_updates: 1, // was 0; Declare V2/V3 always writes one compiled class hash
    n_modified_contracts: 1,
},
``` [1](#0-0) 

Add a regression test that constructs a Declare V3 transaction, calls `estimate_minimal_gas_vector`, and asserts that the returned `l1_data_gas` matches the value computed with `n_compiled_class_hash_updates: 1`.

---

### Proof of Concept

1. Obtain the current block's L1 data gas price.
2. Call `starknet_estimateFee` for a Declare V3 transaction. The returned `data_gas_consumed` will be `N` (underestimated by `2 * DATA_GAS_PER_FIELD_ELEMENT`).
3. Submit the Declare V3 transaction with `l1_data_gas = { max_amount: N, max_price_per_unit: <current_price> }`.
4. The gateway's `check_fee_bounds` computes `estimate_minimal_gas_vector` → `n_compiled_class_hash_updates: 0` → DA cost = `N`. Since `N >= N`, the check passes and the transaction is admitted.
5. During batcher execution, the actual DA cost is `N + 2 * DATA_GAS_PER_FIELD_ELEMENT`, which exceeds `max_amount = N`. The transaction reverts with an insufficient L1 data gas error, but the fee is still charged.

The divergence is exactly `2 * DATA_GAS_PER_FIELD_ELEMENT` L1 data gas units — the cost of writing the `(class_hash, compiled_class_hash)` pair to the DA segment, which every V2/V3 Declare transaction produces but `estimate_minimal_gas_vector` ignores. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/blockifier/src/fee/gas_usage.rs (L22-37)
```rust
pub fn get_onchain_data_segment_length(state_changes_count: &StateChangesCount) -> usize {
    // For each newly modified contract:
    // contract address (1 word).
    // + 1 word with the following info: A flag indicating whether the class hash was updated, the
    // number of entry updates, and the new nonce.
    let mut onchain_data_segment_length = state_changes_count.n_modified_contracts * 2;
    // For each class updated (through a deploy or a class replacement).
    onchain_data_segment_length +=
        state_changes_count.n_class_hash_updates * constants::CLASS_UPDATE_SIZE;
    // For each modified storage cell: key, new value.
    onchain_data_segment_length += state_changes_count.n_storage_updates * 2;
    // For each compiled class updated (through declare): class_hash, compiled_class_hash
    onchain_data_segment_length += state_changes_count.n_compiled_class_hash_updates * 2;

    onchain_data_segment_length
}
```

**File:** crates/blockifier/src/fee/gas_usage.rs (L39-74)
```rust
/// Returns the gas cost of data availability on L1.
pub fn get_da_gas_cost(state_changes_count: &StateChangesCount, use_kzg_da: bool) -> GasVector {
    let onchain_data_segment_length = get_onchain_data_segment_length(state_changes_count);

    let (l1_gas, blob_gas) = if use_kzg_da {
        (
            0_u8.into(),
            u64_from_usize(
                onchain_data_segment_length * eth_gas_constants::DATA_GAS_PER_FIELD_ELEMENT,
            )
            .into(),
        )
    } else {
        // TODO(Yoni, 1/5/2024): count the exact amount of nonzero bytes for each DA entry.
        let naive_cost = onchain_data_segment_length * eth_gas_constants::SHARP_GAS_PER_DA_WORD;

        // For each modified contract, the expected non-zeros bytes in the second word are:
        // 1 bytes for class hash flag; 2 for number of storage updates (up to 64K);
        // 3 for nonce update (up to 16M).
        let modified_contract_cost = eth_gas_constants::get_calldata_word_cost(1 + 2 + 3);
        let modified_contract_discount =
            eth_gas_constants::GAS_PER_MEMORY_WORD - modified_contract_cost;
        let mut discount = state_changes_count.n_modified_contracts * modified_contract_discount;

        // Up to balance of 8*(10**10) ETH.
        let fee_balance_value_cost = eth_gas_constants::get_calldata_word_cost(12);
        discount += eth_gas_constants::GAS_PER_MEMORY_WORD - fee_balance_value_cost;

        // Cost must be non-negative after discount.
        let gas = naive_cost.saturating_sub(discount);

        (u64_from_usize(gas).into(), 0_u8.into())
    };

    GasVector { l1_gas, l1_data_gas: blob_gas, ..Default::default() }
}
```

**File:** crates/blockifier/src/fee/gas_usage.rs (L168-174)
```rust
        Transaction::Declare(_) => StateChangesCount {
            n_storage_updates: 1,
            n_class_hash_updates: 0,
            // TODO(Yoni): BLOCKIFIER-RESET: should be 1.
            n_compiled_class_hash_updates: 0,
            n_modified_contracts: 1,
        },
```

**File:** crates/blockifier/src/transaction/transactions_test.rs (L1846-1852)
```rust
    } else if version == TransactionVersion::TWO || version == TransactionVersion::THREE {
        StateChangesCount {
            n_storage_updates: 1,             // Sender balance.
            n_modified_contracts: 1,          // Nonce.
            n_compiled_class_hash_updates: 1, // Also set compiled class hash.
            ..StateChangesCount::default()
        }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L355-372)
```rust
    pub fn perform_pre_validation_stage<S: State + StateReader>(
        &self,
        state: &mut S,
        tx_context: &TransactionContext,
    ) -> TransactionPreValidationResult<()> {
        let tx_info = &tx_context.tx_info;
        Self::handle_nonce(state, tx_info, self.execution_flags.strict_nonce_check)?;

        if self.execution_flags.charge_fee {
            self.check_fee_bounds(tx_context)?;

            verify_can_pay_committed_bounds(state, tx_context).map_err(Box::new)?;
        }

        self.validate_proof_facts(&tx_context.block_context, state)?;

        Ok(())
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L374-382)
```rust
    fn check_fee_bounds(
        &self,
        tx_context: &TransactionContext,
    ) -> TransactionPreValidationResult<()> {
        let minimal_gas_amount_vector = estimate_minimal_gas_vector(
            &tx_context.block_context,
            self,
            &tx_context.get_gas_vector_computation_mode(),
        );
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L302-354)
```rust
    #[sequencer_latency_histogram(GATEWAY_VALIDATE_TX_LATENCY, true)]
    async fn run_validate_entry_point(
        &mut self,
        executable_tx: &ExecutableTransaction,
        skip_validate: bool,
    ) -> StatefulTransactionValidatorResult<()> {
        let only_query = false;
        let charge_fee = enforce_fee(executable_tx, only_query);
        let strict_nonce_check = false;
        let execution_flags =
            ExecutionFlags { only_query, charge_fee, validate: !skip_validate, strict_nonce_check };

        let account_tx = AccountTransaction { tx: executable_tx.clone(), execution_flags };

        // Build block context.
        let mut versioned_constants = VersionedConstants::get_versioned_constants(
            self.config.versioned_constants_overrides.clone(),
        );
        // The validation of a transaction is not affected by the casm hash migration.
        versioned_constants.disable_casm_hash_migration();

        let mut block_info = self.gateway_fixed_block_state_reader.get_block_info().await?;
        block_info.block_number = block_info.block_number.unchecked_next();
        let block_context = BlockContext::new(
            block_info,
            self.chain_info.clone(),
            versioned_constants,
            BouncerConfig::max(),
        );

        // Move state into the blocking task and run CPU-heavy validation.
        let state_reader_and_contract_manager = self.take_state_reader_and_contract_manager();

        let cur_span = Span::current();
        #[allow(clippy::result_large_err)]
        tokio::task::spawn_blocking(move || {
            cur_span.in_scope(|| {
                let state = CachedState::new(state_reader_and_contract_manager);
                let mut blockifier_validator = StatefulValidator::create(state, block_context);
                blockifier_validator.validate(account_tx)
            })
        })
        .await
        .map_err(|e| StarknetError {
            code: StarknetErrorCode::UnknownErrorCode(
                "StarknetErrorCode.InternalError".to_string(),
            ),
            message: format!("Blocking task join error when running the validate entry point: {e}"),
        })?
        .map_err(|e| StarknetError {
            code: StarknetErrorCode::KnownErrorCode(KnownStarknetErrorCode::ValidateFailure),
            message: e.to_string(),
        })?;
```

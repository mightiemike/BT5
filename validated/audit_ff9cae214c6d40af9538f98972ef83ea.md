### Title
Wrong `class_hash` (0x0) in Broadcasted DeclareV1 Simulation/Fee-Estimation Path - (`crates/apollo_rpc/src/v0_8/api/mod.rs`)

---

### Summary

The `TryFrom<BroadcastedDeclareTransaction>` implementation for `ExecutableTransactionInput` hardcodes `class_hash: ClassHash::default()` (i.e., `0x0`) when converting a `BroadcastedDeclareV1Transaction`. This zero value propagates into the blockifier execution, the transaction hash calculation, the `__validate_declare__` calldata, and the induced state diff returned by `starknet_estimateFee` and `starknet_simulateTransactions`. Every one of those output fields is concretely wrong relative to what would happen on-chain.

---

### Finding Description

In `crates/apollo_rpc/src/v0_8/api/mod.rs`, the `TryFrom<BroadcastedDeclareTransaction>` impl for `ExecutableTransactionInput` constructs a `DeclareTransactionV0V1` with a hardcoded zero class hash:

```rust
Ok(Self::DeclareV1(
    starknet_api::transaction::DeclareTransactionV0V1 {
        max_fee,
        signature,
        nonce,
        // The blockifier doesn't need the class hash, but it uses the SN_API
        // DeclareTransactionV0V1 which requires it.
        class_hash: ClassHash::default(),   // ← always 0x0
        sender_address,
    },
    sn_api_contract_class,
    abi_length,
    false,
))
``` [1](#0-0) 

The comment claims "the blockifier doesn't need the class hash", but this is incorrect. The zero value is consumed in at least three distinct ways:

**1. Transaction hash calculation** — `calc_tx_hash` calls `get_transaction_hash`, which calls `get_declare_transaction_v1_hash`. That function chains `transaction.class_hash.0` into the Pedersen hash:

```rust
.chain(&HashChain::new().chain(&transaction.class_hash.0).get_pedersen_hash())
``` [2](#0-1) 

With `class_hash = 0x0`, the computed `tx_hash` diverges from the real on-chain hash for any non-trivial contract class.

**2. Blockifier execution / `__validate_declare__` calldata** — The blockifier's `DeclareTransaction::run_execute` calls `self.class_hash()` (which reads `DeclareTransactionV0V1.class_hash = 0x0`) to register the class in state and to pass as calldata to `__validate_declare__`: [3](#0-2) 

The account contract's `__validate_declare__` entry point receives `0x0` as the declared class hash instead of the actual hash. Any account that inspects this argument (e.g., to whitelist classes) will behave differently than it would on-chain.

**3. Induced state diff** — `execute_transactions` extracts `class_hash` from the `DeclareTransactionV0V1` struct to populate `deprecated_declared_classes` in the returned `ThinStateDiff`:

```rust
ExecutableTransactionInput::DeclareV1(
    DeclareTransactionV0V1 { class_hash, .. }, _, _, _,
) => Some(*class_hash),   // ← 0x0
``` [4](#0-3) [5](#0-4) 

The simulation output's state diff therefore reports `deprecated_declared_classes: [0x0]` instead of the actual class hash.

The public entry points that trigger this path are `starknet_estimateFee` and `starknet_simulateTransactions`, both of which accept `BroadcastedTransaction::Declare(V1(...))` from any unauthenticated caller: [6](#0-5) [7](#0-6) 

---

### Impact Explanation

Every value the RPC returns for a broadcasted DeclareV1 simulation is wrong:

| Output field | Expected | Actual |
|---|---|---|
| Transaction hash | `H(…, actual_class_hash, …)` | `H(…, 0x0, …)` |
| `__validate_declare__` calldata | `[actual_class_hash]` | `[0x0]` |
| State diff `deprecated_declared_classes` | `[actual_class_hash]` | `[0x0]` |
| Class registered in simulated state | under `actual_class_hash` | under `0x0` |

A client using `simulateTransactions` to pre-flight a DeclareV1 before submitting it will receive a trace and fee estimate computed under a completely different class hash. If the account's `__validate_declare__` is class-hash-sensitive, the fee estimate will be wrong. The state diff is always wrong. The transaction hash is always wrong.

---

### Likelihood Explanation

The path requires only a standard JSON-RPC call to `starknet_estimateFee` or `starknet_simulateTransactions` with a `BROADCASTED_DECLARE_TXN_V1` payload. No privileges, no special state, no operator access. Any user who wants to simulate a Cairo-0 declare transaction hits this path.

---

### Recommendation

Compute the actual deprecated class hash from the contract class before constructing `DeclareTransactionV0V1`. The function `compute_deprecated_class_hash` already exists in the codebase: [8](#0-7) 

Replace `class_hash: ClassHash::default()` with `class_hash: ClassHash(compute_deprecated_class_hash(&sn_api_contract_class)?)` (or an equivalent call to the starknet-API-level hash function). This ensures the transaction hash, the `__validate_declare__` calldata, and the state diff all reflect the real class hash.

---

### Proof of Concept

```rust
// In crates/apollo_rpc/src/v0_8/api/mod.rs (or a test crate)
use starknet_api::transaction::DeclareTransactionV0V1;
use starknet_api::core::ClassHash;
use crate::v0_8::broadcasted_transaction::{
    BroadcastedDeclareTransaction, BroadcastedDeclareV1Transaction,
};
use apollo_rpc_execution::ExecutableTransactionInput;

let broadcasted = BroadcastedDeclareTransaction::V1(BroadcastedDeclareV1Transaction {
    contract_class: /* any non-trivial DeprecatedContractClass */,
    sender_address: /* any address */,
    nonce: Default::default(),
    max_fee: Default::default(),
    signature: Default::default(),
    r#type: Default::default(),
});

let executable: ExecutableTransactionInput = broadcasted.try_into().unwrap();

if let ExecutableTransactionInput::DeclareV1(tx, contract_class, _, _) = executable {
    // tx.class_hash is always 0x0
    assert_eq!(tx.class_hash, ClassHash::default());

    // But the actual hash of the contract class is non-zero
    let actual_hash = compute_deprecated_class_hash(&contract_class).unwrap();
    assert_ne!(Felt::from(tx.class_hash.0), actual_hash,
        "class_hash in executable tx diverges from actual class hash");
}
```

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L494-508)
```rust
                Ok(Self::DeclareV1(
                    starknet_api::transaction::DeclareTransactionV0V1 {
                        max_fee,
                        signature,
                        nonce,
                        // The blockifier doesn't need the class hash, but it uses the SN_API
                        // DeclareTransactionV0V1 which requires it.
                        class_hash: ClassHash::default(),
                        sender_address,
                    },
                    sn_api_contract_class,
                    abi_length,
                    // TODO(yair): pass the right value for only_query field.
                    false,
                ))
```

**File:** crates/starknet_api/src/transaction_hash.rs (L545-562)
```rust
pub(crate) fn get_declare_transaction_v1_hash(
    transaction: &DeclareTransactionV0V1,
    chain_id: &ChainId,
    transaction_version: &TransactionVersion,
) -> Result<TransactionHash, StarknetApiError> {
    Ok(TransactionHash(
        HashChain::new()
        .chain(&DECLARE)
        .chain(&transaction_version.0)
        .chain(transaction.sender_address.0.key())
        .chain(&Felt::ZERO) // No entry point selector in declare transaction.
        .chain(&HashChain::new().chain(&transaction.class_hash.0).get_pedersen_hash())
        .chain(&transaction.max_fee.0.into())
        .chain(&Felt::try_from(chain_id)?)
        .chain(&transaction.nonce.0)
        .get_pedersen_hash(),
    ))
}
```

**File:** crates/blockifier/src/transaction/transactions.rs (L155-193)
```rust
impl<S: State> Executable<S> for DeclareTransaction {
    fn run_execute(
        &self,
        state: &mut S,
        context: &mut EntryPointExecutionContext,
        _remaining_gas: &mut u64,
    ) -> TransactionExecutionResult<Option<CallInfo>> {
        let class_hash = self.class_hash();
        match &self.tx {
            starknet_api::transaction::DeclareTransaction::V0(_)
            | starknet_api::transaction::DeclareTransaction::V1(_) => {
                if context.tx_context.block_context.versioned_constants.disable_cairo0_redeclaration
                {
                    try_declare(self, state, class_hash, None)?
                } else {
                    // We allow redeclaration of the class for backward compatibility.
                    // In the past, we allowed redeclaration of Cairo 0 contracts since there was
                    // no class commitment (so no need to check if the class is already declared).
                    state.set_contract_class(class_hash, self.contract_class().try_into()?)?;
                }
            }
            starknet_api::transaction::DeclareTransaction::V2(DeclareTransactionV2 {
                compiled_class_hash,
                ..
            })
            | starknet_api::transaction::DeclareTransaction::V3(DeclareTransactionV3 {
                compiled_class_hash,
                ..
            }) => {
                if context.tx_context.block_context.versioned_constants.block_casm_hash_v1_declares
                    && self.version() >= TransactionVersion::THREE
                {
                    self.check_compile_class_hash_v2_declaration()?
                }
                try_declare(self, state, class_hash, Some(*compiled_class_hash))?
            }
        }
        Ok(None)
    }
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L740-746)
```rust
            ExecutableTransactionInput::DeclareV1(
                DeclareTransactionV0V1 { class_hash, .. },
                _,
                _,
                _,
            ) => Some(*class_hash),
            _ => None,
```

**File:** crates/apollo_rpc_execution/src/execution_utils.rs (L130-145)
```rust
pub fn induced_state_diff(
    transactional_state: &mut CachedState<MutRefState<'_, CachedState<ExecutionStateReader>>>,
    deprecated_declared_class_hash: Option<ClassHash>,
) -> ExecutionResult<ThinStateDiff> {
    let blockifier_state_diff =
        CommitmentStateDiff::from(transactional_state.to_state_diff()?.state_maps);

    Ok(ThinStateDiff {
        deployed_contracts: blockifier_state_diff.address_to_class_hash,
        storage_diffs: blockifier_state_diff.storage_updates,
        class_hash_to_compiled_class_hash: blockifier_state_diff.class_hash_to_compiled_class_hash,
        deprecated_declared_classes: deprecated_declared_class_hash
            .map_or_else(Vec::new, |class_hash| vec![class_hash]),
        nonces: blockifier_state_diff.address_to_nonce,
    })
}
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L997-1019)
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
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1066-1075)
```rust
    #[instrument(skip(self, transactions), level = "debug", err, ret)]
    async fn simulate_transactions(
        &self,
        block_id: BlockId,
        transactions: Vec<BroadcastedTransaction>,
        simulation_flags: Vec<SimulationFlag>,
    ) -> RpcResult<Vec<SimulatedTransaction>> {
        trace!("Simulating transactions: {:#?}", transactions);
        let executable_txns =
            transactions.into_iter().map(|tx| tx.try_into()).collect::<Result<_, _>>()?;
```

**File:** crates/starknet_os/src/hints/hint_implementation/deprecated_compiled_class/class_hash.rs (L80-102)
```rust
pub fn compute_deprecated_class_hash(
    contract_class: &ContractClass,
) -> Result<Felt, HintedClassHashError> {
    let hinted_class_hash = compute_cairo_hinted_class_hash(contract_class)?;
    let contract_definition_vec = serde_json::to_vec(contract_class)?;
    let contract_definition: CairoContractDefinition<'_> =
        serde_json::from_slice(&contract_definition_vec)?;

    let FlatEntryPointFelts { external, l1_handler, constructor } =
        get_flat_entry_point_felts(&contract_definition.entry_points_by_type);
    let builtins = ascii_strs_as_felts(&contract_definition.program.builtins);
    let bytecode = hex_strs_as_felts(&contract_definition.program.data);

    let mut hash_state = HashState::<Pedersen>::new();
    hash_state.update_single(&DEPRECATED_COMPILED_CLASS_VERSION);
    hash_state.update_with_hashchain(&external);
    hash_state.update_with_hashchain(&l1_handler);
    hash_state.update_with_hashchain(&constructor);
    hash_state.update_with_hashchain(&builtins);
    hash_state.update_single(&hinted_class_hash);
    hash_state.update_with_hashchain(&bytecode);
    Ok(hash_state.finalize())
}
```

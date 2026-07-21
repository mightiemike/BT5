### Title
`VirtualBlockExecutor::execute()` Ignores `NativeClassesWhitelist`, Selecting Wrong Compiled Class for Proof Re-execution — (`File: crates/starknet_transaction_prover/src/running/virtual_block_executor.rs`)

### Summary

The transaction prover's `VirtualBlockExecutor::execute()` constructs its `StateReaderAndContractManager` via the `::new()` constructor, which hardcodes `NativeClassesWhitelist::All`. This bypasses the operator-configured `Limited` whitelist that the batcher enforces during original block execution. As a result, the prover re-executes transactions using Cairo Native compiled classes for contracts that the batcher executed with CASM, producing a divergent execution path for proof generation.

### Finding Description

`StateReaderAndContractManager::new()` unconditionally sets `NativeClassesWhitelist::All`: [1](#0-0) 

The batcher's `BlockBuilderFactory::preprocess_and_create_transaction_executor()` correctly uses `new_with_native_classes_whitelist()`, threading the operator-configured whitelist through: [2](#0-1) 

The whitelist is sourced from `BatcherDynamicConfig.native_classes_whitelist`, which in production mainnet is set to a single specific class hash: [3](#0-2) 

However, `VirtualBlockExecutor::execute()` — the prover's re-execution path — calls `StateReaderAndContractManager::new()` directly, discarding the whitelist entirely: [4](#0-3) 

`NativeClassManager::get_runnable()` uses the whitelist to decide whether to return a `V1Native` or fall back to `V1` (CASM) for a given class hash: [5](#0-4) 

With `NativeClassesWhitelist::All` in the prover, every cached Cairo 1 class is served as `V1Native`. With `NativeClassesWhitelist::Limited([0x054c...])` in the batcher, only the one whitelisted class is served as `V1Native`; all others fall back to CASM. Any transaction touching a non-whitelisted Cairo 1 class is therefore executed under a different compiled class variant in the prover than in the batcher.

### Impact Explanation

**Critical. Wrong compiled class, CASM/native artifact, or contract code selected for execution.**

The prover generates a proof over a Native execution of a contract that the batcher executed as CASM. If the Native-compiled artifact produces any divergent result — different storage writes, events, return values, or gas consumption — the proof attests to a state transition that was never committed on-chain. The `executed_class_hashes` set collected by the prover also reflects the Native variant, not the CASM variant used during sequencing.

### Likelihood Explanation

The mainnet deployment explicitly configures `native_classes_whitelist` to a single class hash, meaning every other Cairo 1 contract in every block is subject to this divergence whenever the prover re-executes it. The trigger requires no privileged access: any user transaction invoking a non-whitelisted Cairo 1 contract reaches this code path.

### Recommendation

Pass the operator-configured `NativeClassesWhitelist` into `VirtualBlockExecutor::execute()` (or into the `VirtualBlockExecutor` trait implementor at construction time) and use `StateReaderAndContractManager::new_with_native_classes_whitelist()` instead of `::new()`:

```rust
// In VirtualBlockExecutor::execute():
let state_reader_and_contract_manager =
    StateReaderAndContractManager::new_with_native_classes_whitelist(
        state_reader,
        contract_class_manager,
        self.native_classes_whitelist(), // sourced from config
        None,
    );
```

The `RpcVirtualBlockExecutorConfig` should carry a `native_classes_whitelist` field, defaulting to `NativeClassesWhitelist::All` only when no restriction is configured, and the `BlockBuilderFactoryTrait::create_block_builder` whitelist parameter should be the canonical source for both paths.

### Proof of Concept

1. Configure the sequencer with `native_classes_whitelist = '["0xABCD"]'` (a single whitelisted class).
2. Submit a transaction that invokes a Cairo 1 contract whose class hash is **not** `0xABCD`.
3. The batcher executes it via `StateReaderAndContractManager::new_with_native_classes_whitelist(..., Limited(["0xABCD"]), ...)` → `get_runnable` returns `V1` (CASM) for the non-whitelisted class.
4. The prover re-executes it via `StateReaderAndContractManager::new(...)` → `get_runnable` returns `V1Native` (Cairo Native) for the same class.
5. If the Native artifact produces any output difference from CASM (e.g., due to a compiler bug or uninitialized memory behavior), the proof covers a different execution than what was sequenced and committed. [6](#0-5) [1](#0-0) [7](#0-6)

### Citations

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L42-53)
```rust
    pub fn new(
        state_reader: S,
        contract_class_manager: ContractClassManager,
        class_cache_metrics: Option<CacheMetrics>,
    ) -> Self {
        Self::new_with_native_classes_whitelist(
            state_reader,
            contract_class_manager,
            NativeClassesWhitelist::All,
            class_cache_metrics,
        )
    }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L758-786)
```rust
        native_classes_whitelist: NativeClassesWhitelist,
        runtime: tokio::runtime::Handle,
    ) -> BlockBuilderResult<ConcurrentTransactionExecutor<ApolloStateReaderAndContractManager>>
    {
        info!(
            "preprocess and create transaction executor for block {}",
            block_metadata.block_info.block_number
        );
        let height = block_metadata.block_info.block_number;
        let block_builder_config = self.block_builder_config.clone();
        let versioned_constants = VersionedConstants::get_versioned_constants(
            block_builder_config.versioned_constants_overrides,
        );
        let block_context = BlockContext::new(
            block_metadata.block_info,
            block_builder_config.chain_info,
            versioned_constants,
            block_builder_config.bouncer_config,
        );

        let class_reader = Some(ClassReader { reader: self.class_manager_client.clone(), runtime });
        let apollo_reader =
            ApolloReader::new_with_class_reader(self.storage_reader.clone(), height, class_reader);
        let state_reader = StateReaderAndContractManager::new_with_native_classes_whitelist(
            apollo_reader,
            self.contract_class_manager.clone(),
            native_classes_whitelist,
            Some(BATCHER_CLASS_CACHE_METRICS),
        );
```

**File:** deployments/sequencer/configs/overlays/hybrid/mainnet/common.yaml (L9-9)
```yaml
    native_classes_whitelist: '["0x054c5afe61ed27be53b1e4dec5707209a9fcabdb14712fb800fbc60439090115"]'
```

**File:** crates/starknet_transaction_prover/src/running/virtual_block_executor.rs (L253-256)
```rust
        // Create state reader with contract manager.
        let state_reader_and_contract_manager =
            StateReaderAndContractManager::new(state_reader, contract_class_manager, None);

```

**File:** crates/blockifier/src/state/native_class_manager.rs (L141-150)
```rust
        let cached_class = match cached_class {
            CompiledClasses::V1Native(CachedCairoNative::Compiled(native))
                if !native_classes_whitelist.contains(class_hash) =>
            {
                CompiledClasses::into_non_native_class(native)
            }
            CompiledClasses::V1Native(..) | CompiledClasses::V1(..) | CompiledClasses::V0(..) => {
                cached_class
            }
        };
```

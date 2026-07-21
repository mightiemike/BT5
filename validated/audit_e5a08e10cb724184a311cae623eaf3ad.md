### Title
`StateReaderAndContractManager::new` Hardcodes `NativeClassesWhitelist::All`, Bypassing Configured Compiler-Selection Whitelist in Production Execution Paths — (File: `crates/blockifier/src/state/state_reader_and_contract_manager.rs`)

### Summary
`StateReaderAndContractManager::new` unconditionally sets `NativeClassesWhitelist::All`, meaning every contract class is eligible for Cairo-native (LLVM-compiled) execution regardless of any operator-configured whitelist. Production callers in the transaction prover (`virtual_block_executor.rs`) and the Python block executor (`py_block_executor.rs`) use this unrestricted constructor instead of `new_with_native_classes_whitelist`. When an operator restricts native execution to a `Limited` set of class hashes, those callers silently ignore the restriction and select the native artifact for every class, diverging from the CASM path used by the batcher/gateway.

### Finding Description
`StateReaderAndContractManager` holds a `native_classes_whitelist` field that is consulted on every `get_compiled_class` call to decide whether to return a `RunnableCompiledClass::V1Native` (LLVM) or `RunnableCompiledClass::V1` (CASM) artifact. [1](#0-0) 

The public `new` constructor hard-codes `NativeClassesWhitelist::All`: [2](#0-1) 

A second constructor, `new_with_native_classes_whitelist`, exists precisely to honour a caller-supplied whitelist: [3](#0-2) 

The batcher and gateway configs expose `NativeClassesWhitelist` as a configurable field (defaulting to `All`, but supporting `Limited`): [4](#0-3) 

However, the transaction-prover virtual block executor calls the unrestricted `new`: [5](#0-4) 

The Python block executor (`py_block_executor.rs`) does the same. Neither path accepts or forwards a `NativeClassesWhitelist` from the node configuration.

### Impact Explanation
When an operator sets `NativeClassesWhitelist::Limited([…])` for the batcher/gateway, those components execute non-whitelisted classes with CASM. The prover and Python executor, using `NativeClassesWhitelist::All`, select the native artifact for the same classes. If the Cairo-native compiler produces any divergent result for a given class (a known risk during the native rollout period), the prover generates a proof over a different execution trace than the one the sequencer committed to. This satisfies the Critical impact category: **wrong compiled class / CASM/native artifact selected for execution**, producing a wrong state, receipt, or revert result from blockifier/execution logic.

### Likelihood Explanation
Likelihood is **low-to-medium**. It requires an operator to explicitly configure `NativeClassesWhitelist::Limited`, which is a non-default setting. However, the whitelist feature exists specifically to gate native execution during the rollout phase, so operators who use it have a reasonable expectation that all execution paths respect it. The divergence is silent — no error is raised.

### Recommendation
Replace the bare `StateReaderAndContractManager::new` calls in `virtual_block_executor.rs` and `py_block_executor.rs` with `new_with_native_classes_whitelist`, threading the configured `NativeClassesWhitelist` from the node/prover config into those constructors. Alternatively, deprecate `new` and require all callers to supply an explicit whitelist, mirroring the pattern already used by the batcher and gateway.

### Proof of Concept
1. Configure the node with `native_classes_whitelist = Limited(["0xABC"])` in the batcher config.
2. Submit an invoke transaction calling contract `0xDEF` (not in the whitelist).
3. The batcher executes `0xDEF` via CASM (`RunnableCompiledClass::V1`).
4. The prover's `RpcVirtualBlockExecutor::execute` constructs `StateReaderAndContractManager::new(…)` (line 255 of `virtual_block_executor.rs`), which sets `NativeClassesWhitelist::All`.
5. The prover executes `0xDEF` via the native artifact (`RunnableCompiledClass::V1Native`).
6. Any semantic divergence between the two artifacts produces a proof over a different execution trace than the committed state, yielding a wrong receipt or state root. [5](#0-4) [2](#0-1) [6](#0-5)

### Citations

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L34-53)
```rust
pub struct StateReaderAndContractManager<S: FetchCompiledClasses> {
    pub state_reader: S,
    contract_class_manager: ContractClassManager,
    native_classes_whitelist: NativeClassesWhitelist,
    class_cache_metrics: Option<CacheMetrics>,
}

impl<S: FetchCompiledClasses> StateReaderAndContractManager<S> {
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

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L55-62)
```rust
    pub fn new_with_native_classes_whitelist(
        state_reader: S,
        contract_class_manager: ContractClassManager,
        native_classes_whitelist: NativeClassesWhitelist,
        class_cache_metrics: Option<CacheMetrics>,
    ) -> Self {
        Self { state_reader, contract_class_manager, native_classes_whitelist, class_cache_metrics }
    }
```

**File:** crates/blockifier/src/blockifier/config.rs (L188-253)
```rust
#[derive(Clone, Debug, PartialEq)]
pub enum NativeClassesWhitelist {
    All,
    Limited(Vec<ClassHash>),
}

impl NativeClassesWhitelist {
    pub const SER_PARAM_DESCRIPTION: &str = "Specifies whether to execute all class hashes or \
                                             only specific ones using Cairo native. If limited, a \
                                             specific list of class hashes is provided.";

    pub fn ser_param(&self) -> (String, SerializedParam) {
        ser_param(
            "native_classes_whitelist",
            &self,
            Self::SER_PARAM_DESCRIPTION,
            ParamPrivacyInput::Public,
        )
    }
}

impl<'de> Deserialize<'de> for NativeClassesWhitelist {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let raw: String = <String as serde::Deserialize>::deserialize(deserializer)?;

        if raw == "All" {
            return Ok(NativeClassesWhitelist::All);
        }
        // Support stringified JSON array: "[\"0x..\", \"0x..\"]"
        match serde_json::from_str::<Vec<ClassHash>>(&raw) {
            Ok(vec) => Ok(NativeClassesWhitelist::Limited(vec)),
            Err(_) => Err(de::Error::custom(format!(
                "invalid native_classes_whitelist string: expected \"All\" or stringified JSON \
                 array, (i.e., \"[\\\"0x..\\\", \\\"0x..\\\"]\") got: {}",
                raw
            ))),
        }
    }
}

impl Serialize for NativeClassesWhitelist {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        match self {
            NativeClassesWhitelist::All => serializer.serialize_str("All"),
            NativeClassesWhitelist::Limited(vec) => {
                let json = serde_json::to_string(vec)
                    .expect("Failed to stringify whitelist to JSON array");
                serializer.serialize_str(&json)
            }
        }
    }
}

impl NativeClassesWhitelist {
    pub fn contains(&self, class_hash: &ClassHash) -> bool {
        match self {
            NativeClassesWhitelist::All => true,
            NativeClassesWhitelist::Limited(contracts) => contracts.contains(class_hash),
        }
    }
```

**File:** crates/starknet_transaction_prover/src/running/virtual_block_executor.rs (L253-256)
```rust
        // Create state reader with contract manager.
        let state_reader_and_contract_manager =
            StateReaderAndContractManager::new(state_reader, contract_class_manager, None);

```

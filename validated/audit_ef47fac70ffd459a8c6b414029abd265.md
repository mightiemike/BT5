### Title
`SyncStateReader::get_compiled_class_hash` Panics on Any Declare V2/V3 Transaction, Crashing the Gateway — (`File: crates/apollo_gateway/src/sync_state_reader.rs`)

---

### Summary

`SyncStateReader`, the production state reader used by the Apollo Gateway's stateful transaction validator, implements `BlockifierStateReader::get_compiled_class_hash` with a bare `todo!()`. Any Declare V2/V3 transaction submitted to the Gateway triggers this code path during blockifier execution, causing the Gateway process to panic and freezing all transaction admission until the process is restarted.

---

### Finding Description

`SyncStateReader` implements the `BlockifierStateReader` trait for the Gateway's live state-sync-backed state. All methods are implemented except `get_compiled_class_hash`, which is left as `todo!()`:

```rust
fn get_compiled_class_hash(&self, _class_hash: ClassHash) -> StateResult<CompiledClassHash> {
    todo!()
}
``` [1](#0-0) 

`SyncStateReader` is the concrete type returned by `SyncStateReaderFactory::get_blockifier_state_reader_and_gateway_fixed_block_from_latest_block` and is wrapped into `SyncOrGenesisStateReader`: [2](#0-1) 

`SyncOrGenesisStateReader::get_compiled_class_hash` unconditionally delegates to `SyncStateReader::get_compiled_class_hash`: [3](#0-2) 

This `SyncOrGenesisStateReader` is passed into `StatefulTransactionValidator` as the `TGatewayStateReaderWithCompiledClasses` type parameter, which wraps it in a `StateReaderAndContractManager`. That wrapper's `get_compiled_class_hash` delegates directly to the inner state reader: [4](#0-3) 

The `CachedState` wrapping this reader calls through to `get_compiled_class_hash` on a cache miss: [5](#0-4) 

During blockifier execution of a Declare V2/V3 transaction, the compiled class hash is read from state to verify the `compiled_class_hash` field in the transaction. This cache miss triggers the `todo!()` panic in `SyncStateReader`.

---

### Impact Explanation

The `todo!()` macro panics unconditionally at runtime. In the Gateway's async task context, this unwinds the validation task and crashes the Gateway component. All subsequent transaction admission is frozen until the process is restarted. This matches:

> **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

Every Declare V2/V3 transaction (the standard transaction type for deploying Cairo 1 contracts) is a valid, unprivileged trigger. No special permissions or malformed bytes are required — a well-formed Declare V3 transaction from any user is sufficient.

---

### Likelihood Explanation

Declare V2/V3 transactions are the standard mechanism for deploying Cairo 1 contracts on Starknet. Any user deploying a new contract class triggers this path. Likelihood is **high** because this is a routine, unprivileged operation on any live Starknet sequencer node.

---

### Recommendation

Implement `SyncStateReader::get_compiled_class_hash` by querying the `state_sync_client` for the compiled class hash at `self.block_number`, analogous to how `get_nonce_at` and `get_class_hash_at` are implemented. The `StateSyncClient` trait already exposes the necessary interface. Alternatively, if the method is genuinely unreachable in the current Gateway validation flow, replace `todo!()` with a proper `Err(StateError::StateReadError(...))` return so the error propagates gracefully instead of panicking.

---

### Proof of Concept

1. Start the Apollo sequencer node with the Gateway component enabled.
2. Submit a valid Declare V3 transaction (any Cairo 1 contract class) to the Gateway's `add_declare_transaction` endpoint.
3. The Gateway's stateful validator calls `run_validate_entry_point` → blockifier executes the declare → `CachedState::get_compiled_class_hash` is called → cache miss → `SyncStateReader::get_compiled_class_hash` → `todo!()` → **panic**.
4. The Gateway process crashes. All subsequent transaction submissions are rejected until the process is restarted. [1](#0-0) [3](#0-2) [5](#0-4)

### Citations

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L197-199)
```rust
    fn get_compiled_class_hash(&self, _class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        todo!()
    }
```

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L443-450)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        match self {
            Self::Sync(state_reader) => state_reader.get_compiled_class_hash(class_hash),
            Self::Genesis(genesis_state_reader) => {
                genesis_state_reader.get_compiled_class_hash(class_hash)
            }
        }
    }
```

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L539-549)
```rust
        let blockifier_state_reader = SyncStateReader::from_number(
            self.shared_state_sync_client.clone(),
            self.class_manager_client.clone(),
            latest_block_number,
            self.runtime.clone(),
        );
        let gateway_fixed_block_sync_state_client = GatewayFixedBlockSyncStateClient::new(
            self.shared_state_sync_client.clone(),
            latest_block_number,
        );
        Ok((blockifier_state_reader.into(), gateway_fixed_block_sync_state_client.into()))
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L155-157)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        self.state_reader.get_compiled_class_hash(class_hash)
    }
```

**File:** crates/blockifier/src/state/cached_state.rs (L204-216)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        let mut cache = self.cache.borrow_mut();

        if cache.get_compiled_class_hash(class_hash).is_none() {
            let compiled_class_hash = self.state.get_compiled_class_hash(class_hash)?;
            cache.set_compiled_class_hash_initial_value(class_hash, compiled_class_hash);
        }

        let compiled_class_hash = cache
            .get_compiled_class_hash(class_hash)
            .unwrap_or_else(|| panic!("Cannot retrieve '{class_hash:?}' from the cache."));
        Ok(*compiled_class_hash)
    }
```

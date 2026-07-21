### Title
Cairo 0 Class Declaration Not Verified Against Block State in `ExecutionStateReader::get_compiled_class` — (File: `crates/apollo_rpc_execution/src/state_reader.rs`)

---

### Summary

`ExecutionStateReader::get_compiled_class` applies a block-scoped declaration check (`is_contract_class_declared`) for Cairo 1 classes fetched from the class manager, but unconditionally returns Cairo 0 (deprecated) classes without any such check. A Cairo 0 class that is present in the class manager — because it was declared in a later block or in a since-reverted block — will be returned and executed for any historical or pending RPC query, producing an authoritative-looking but wrong execution result.

---

### Finding Description

The external bug's invariant is: a mapping value can be in two valid states (`NATIVE_STATUS` or `DEPLOYED_STATUS`), but the bridging function only checks one of them, causing the other to be silently mishandled. The sequencer analog is structurally identical: a compiled class can be in two valid states (`ContractClass::V1` / Cairo 1, or `ContractClass::V0` / Cairo 0), but the declaration-at-block check is only applied to one of them.

In `ExecutionStateReader::get_compiled_class`, when `class_manager_handle` is present, the code fetches the class from the class manager and then branches on its type:

```rust
return match contract_class {
    ContractClass::V1(casm_contract_class) => {
        let is_declared = is_contract_class_declared(
            &self.storage_reader.begin_ro_txn()...,
            &class_hash,
            self.state_number,          // ← block-scoped check
        )?;
        if is_declared {
            Ok(RunnableCompiledClass::V1(casm_contract_class.try_into()?))
        } else {
            Err(StateError::UndeclaredClassHash(class_hash))
        }
    }
    // TODO(shahak): Verify cairo0 as well after get_class_definition_block_number is fixed.
    ContractClass::V0(deprecated_contract_class) => {
        Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))   // ← no check
    }
};
``` [1](#0-0) 

The class manager is a global, block-agnostic cache. It stores a Cairo 0 class as soon as that class is first processed — regardless of which block declared it and regardless of whether that block is still canonical. The `state_number` field of `ExecutionStateReader` encodes the specific historical block being queried. For Cairo 1 classes, `is_contract_class_declared` enforces that the class was declared at or before `state_number`. For Cairo 0 classes, this enforcement is absent.

The same asymmetry exists in `StateReaderAndContractManager::get_compiled_from_class_manager`, where the `is_declared` verification is explicitly skipped for `RunnableCompiledClass::V0`:

```rust
match &runnable_class {
    RunnableCompiledClass::V0(_) => {}   // ← no is_declared call
    _ => {
        if !self.state_reader.is_declared(class_hash)? {
            return Err(StateError::UndeclaredClassHash(class_hash));
        }
    }
}
``` [2](#0-1) 

The `FetchCompiledClasses::is_declared` trait is explicitly documented as only covering Cairo 1 classes:

```rust
/// Returns whether the given class hash corresponds to a declared Cairo 1 class.
/// Cairo 0 classes always return `false`.
fn is_declared(&self, class_hash: ClassHash) -> StateResult<bool>;
``` [3](#0-2) 

And `SyncStateReader::is_declared` calls only `is_cairo_1_class_declared_at`, not the broader `is_class_declared_at` that covers both Cairo 0 and Cairo 1: [4](#0-3) 

By contrast, `apollo_state_sync` exposes `is_class_declared_at` which checks both Cairo 0 and Cairo 1 classes: [5](#0-4) 

---

### Impact Explanation

**RPC execution, fee estimation, tracing, and simulation return an authoritative-looking wrong value.**

Concrete scenario:

1. Cairo 0 class `C` is declared in block `N`.
2. A user submits an RPC call (`starknet_call`, `starknet_estimateFee`, `starknet_simulateTransactions`) targeting block `M < N`, where `C` was not yet declared.
3. `ExecutionStateReader` is constructed with `state_number = M`.
4. `get_compiled_class(C)` is called. The class manager already holds `C` (populated when block `N` was processed).
5. The Cairo 1 branch would call `is_contract_class_declared` with `state_number = M` and return `UndeclaredClassHash`. The Cairo 0 branch skips this check and returns the class.
6. The RPC call executes `C` as if it were declared at block `M`, producing a wrong return value, wrong fee estimate, or wrong trace.

The same scenario applies after a chain reorganization: a Cairo 0 class cached from a reverted block remains in the class manager and is returned without verification for any subsequent query.

---

### Likelihood Explanation

- The class manager is enabled in production deployments.
- Historical RPC queries (`block_id = block_number`) are a standard, unprivileged operation.
- Cairo 0 contracts remain in active use on Starknet mainnet.
- The TODO comment in the source confirms the developers are aware the check is missing and intend to add it.
- No special privilege or malicious peer is required; any RPC client can trigger this by querying a block number before a Cairo 0 class was declared.

---

### Recommendation

Apply the same `is_contract_class_declared` (or equivalent deprecated-class declaration check) to the `ContractClass::V0` branch in `ExecutionStateReader::get_compiled_class`:

```rust
ContractClass::V0(deprecated_contract_class) => {
    let is_declared = is_deprecated_contract_class_declared(
        &self.storage_reader.begin_ro_txn()...,
        &class_hash,
        self.state_number,
    )?;
    if is_declared {
        Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))
    } else {
        Err(StateError::UndeclaredClassHash(class_hash))
    }
}
```

Similarly, extend `FetchCompiledClasses::is_declared` (or add a parallel `is_cairo0_declared`) and apply it in `StateReaderAndContractManager::get_compiled_from_class_manager` for the `V0` branch. The `is_class_declared_at` method already exists in `apollo_state_sync` and covers both class types.

---

### Proof of Concept

1. Deploy a Starknet node with the class manager enabled.
2. Declare a Cairo 0 contract in block `N` (e.g., block 100).
3. Issue `starknet_call` targeting block `M = N - 1` (e.g., block 99) with a contract whose class hash is the Cairo 0 class declared in block 100.
4. Observe that the call succeeds and returns execution output, rather than returning `CLASS_HASH_NOT_FOUND` / `UndeclaredClassHash`.
5. For Cairo 1: repeat the same experiment with a Cairo 1 class declared in block 100 and queried at block 99 — the node correctly returns `UndeclaredClassHash`, confirming the asymmetry.

The divergence is rooted in the missing `is_contract_class_declared` call at: [6](#0-5)

### Citations

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L121-141)
```rust
            return match contract_class {
                ContractClass::V1(casm_contract_class) => {
                    let is_declared = is_contract_class_declared(
                        &self.storage_reader.begin_ro_txn().map_err(storage_err_to_state_err)?,
                        &class_hash,
                        self.state_number,
                    )
                    .map_err(|e| StateError::StateReadError(e.to_string()))?;

                    if is_declared {
                        Ok(RunnableCompiledClass::V1(casm_contract_class.try_into()?))
                    } else {
                        Err(StateError::UndeclaredClassHash(class_hash))
                    }
                }
                // TODO(shahak): Verify cairo0 as well after get_class_definition_block_number is
                // fixed.
                ContractClass::V0(deprecated_contract_class) => {
                    Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))
                }
            };
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L19-21)
```rust
    /// Returns whether the given class hash corresponds to a declared Cairo 1 class.
    /// Cairo 0 classes always return `false`.
    fn is_declared(&self, class_hash: ClassHash) -> StateResult<bool>;
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L73-83)
```rust
            match &runnable_class {
                RunnableCompiledClass::V0(_) => {}
                _ => {
                    // The Cairo1 class is cached; verify it is declared,
                    // since existence in the cache does not guarantee that
                    // (it might contain a declared class from a reverted block, for example).
                    if !self.state_reader.is_declared(class_hash)? {
                        return Err(StateError::UndeclaredClassHash(class_hash));
                    }
                }
            }
```

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L125-132)
```rust
    /// Returns whether the given Cairo1 class is declared.
    fn is_declared(&self, class_hash: ClassHash) -> StateResult<bool> {
        self.runtime
            .block_on(
                self.state_sync_client.is_cairo_1_class_declared_at(self.block_number, class_hash),
            )
            .map_err(|e| StateError::StateReadError(e.to_string()))
    }
```

**File:** crates/apollo_state_sync/src/lib.rs (L335-356)
```rust
    async fn is_class_declared_at(
        &self,
        block_number: BlockNumber,
        class_hash: ClassHash,
    ) -> StateSyncResult<bool> {
        if self.is_cairo_1_class_declared_at(block_number, class_hash).await? {
            return Ok(true);
        }

        let storage_reader = self.storage_reader.clone();
        // TODO(noamsp): Add unit testing for cairo0
        let deprecated_class_definition_block_number_opt = storage_reader
            .begin_ro_txn()?
            .get_state_reader()?
            .get_deprecated_class_definition_block_number(&class_hash)?;

        Ok(deprecated_class_definition_block_number_opt.is_some_and(
            |deprecated_class_definition_block_number| {
                deprecated_class_definition_block_number <= block_number
            },
        ))
    }
```

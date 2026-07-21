### Title
Cairo 0 (Deprecated) Class Declaration Not Verified Against State Number in RPC Execution State Reader - (File: crates/apollo_rpc_execution/src/state_reader.rs)

### Summary

In `apollo_rpc_execution/src/state_reader.rs`, the `get_compiled_class` function checks whether a Cairo 1 class is actually declared at the queried `state_number` before returning it from the class manager cache. The identical check is explicitly skipped for Cairo 0 (deprecated) classes, with a `TODO` comment acknowledging the gap. As a result, a Cairo 0 class that exists in the class manager cache but was not declared at the queried block can be silently returned and executed, causing RPC execution, fee estimation, and simulation to produce authoritative-looking wrong values for historical block queries.

### Finding Description

The `get_compiled_class` implementation in `ExecutionStateReader` has two branches when the class manager handle is present:

**Cairo 1 path — existence check performed:**
```rust
ContractClass::V1(casm_contract_class) => {
    let is_declared = is_contract_class_declared(
        &self.storage_reader.begin_ro_txn()...,
        &class_hash,
        self.state_number,   // ← checks declaration at the queried block
    )?;
    if is_declared {
        Ok(RunnableCompiledClass::V1(casm_contract_class.try_into()?))
    } else {
        Err(StateError::UndeclaredClassHash(class_hash))
    }
}
```

**Cairo 0 path — existence check absent:**
```rust
// TODO(shahak): Verify cairo0 as well after get_class_definition_block_number is
// fixed.
ContractClass::V0(deprecated_contract_class) => {
    Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))
}
``` [1](#0-0) 

The class manager is a state-number-agnostic cache. Its comment for the Cairo 1 branch explicitly states: *"existence in the cache does not guarantee that (it might contain a declared class from a reverted block, for example)."* The same reasoning applies to Cairo 0 classes, but the guard is absent. [2](#0-1) 

The `FetchCompiledClasses::is_declared` trait documents that Cairo 0 classes always return `false`, meaning the declaration-at-state-number check is intentionally bypassed for the deprecated class type across the codebase. [3](#0-2) 

### Impact Explanation

The `ExecutionStateReader` is used by `starknet_call`, `starknet_estimateFee`, `starknet_simulateTransactions`, and `starknet_traceTransaction`. When a caller queries any of these at a historical `block_id` (e.g., block N−1), and a Cairo 0 class was declared at block N (or in a reverted block), the class manager may already hold it in cache. The Cairo 0 branch returns it without checking `state_number`, so execution proceeds against a class that did not exist at the queried state. The RPC response carries an authoritative-looking result (no error, plausible gas/return values) that is factually wrong for the requested block.

This matches the allowed impact: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

### Likelihood Explanation

The class manager is populated during normal sequencing and syncing. Any node that has processed a block declaring a Cairo 0 class and then serves historical RPC queries for blocks before that declaration is exposed. The attacker-controlled input is simply the `block_id` parameter of any of the affected RPC methods — no privileged access is required. The scenario is reachable on any production node serving historical queries.

### Recommendation

**Short term:** Apply the same `is_contract_class_declared` guard to the `ContractClass::V0` branch in `get_compiled_class` in `crates/apollo_rpc_execution/src/state_reader.rs`. The `deprecated_declared_classes_block_table` already stores the declaration block number per class hash, so the check can be implemented analogously to the Cairo 1 path once `get_class_definition_block_number` is fixed for deprecated classes (as the TODO notes).

**Long term:** Resolve the underlying `get_class_definition_block_number` issue for Cairo 0 classes so that the unified `is_contract_class_declared` helper covers both class types, eliminating the asymmetry.

### Proof of Concept

1. Node syncs to block N, which declares Cairo 0 class `C` with hash `H`. The class manager caches `H → ContractClass::V0(...)`.
2. Client calls `starknet_call` (or `starknet_estimateFee`) with `block_id = N-1` and a calldata that triggers execution of class `H`.
3. `ExecutionStateReader::get_compiled_class(H)` is invoked with `state_number = right_after_block(N-1)`.
4. The class manager returns `ContractClass::V0(...)` for `H`.
5. The Cairo 0 branch is taken; no `is_contract_class_declared` check is performed.
6. Execution proceeds using class `H` even though it was not declared at block N−1.
7. The RPC response returns execution results (return values, gas, events) as if the class existed at block N−1, which is incorrect.

The Cairo 1 branch at the same code location would have called `is_contract_class_declared(..., state_number)`, found the class undeclared, and returned `StateError::UndeclaredClassHash(H)` — the correct behavior. [4](#0-3)

### Citations

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L115-161)
```rust
        if let Some((class_manager_client, run_time_handle)) = &self.class_manager_handle {
            let contract_class = run_time_handle
                .block_on(class_manager_client.get_executable(class_hash))
                .map_err(|e| StateError::StateReadError(e.to_string()))?
                .ok_or(StateError::UndeclaredClassHash(class_hash))?;

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
        }

        match get_contract_class(
            &self.storage_reader.begin_ro_txn().map_err(storage_err_to_state_err)?,
            &class_hash,
            self.state_number,
        ) {
            Ok(Some(contract_class)) => Ok(contract_class),
            Ok(None) => Err(StateError::UndeclaredClassHash(class_hash)),
            Err(ExecutionUtilsError::CasmTableNotSynced) => {
                self.missing_compiled_class.set(Some(class_hash));
                Err(StateError::StateReadError("Casm table not fully synced".to_string()))
            }
            Err(ExecutionUtilsError::ProgramError(err)) => Err(StateError::ProgramError(err)),
            Err(ExecutionUtilsError::StorageError(err)) => Err(storage_err_to_state_err(err)),
            Err(ExecutionUtilsError::SierraValidationError(err)) => {
                Err(StateError::StarknetApiError(err))
            }
        }
    }
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L16-22)
```rust
pub trait FetchCompiledClasses: StateReader {
    fn get_compiled_classes(&self, class_hash: ClassHash) -> StateResult<CompiledClasses>;

    /// Returns whether the given class hash corresponds to a declared Cairo 1 class.
    /// Cairo 0 classes always return `false`.
    fn is_declared(&self, class_hash: ClassHash) -> StateResult<bool>;
}
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L73-87)
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
            self.increment_cache_hit_metric();
            self.update_native_metrics(&runnable_class);
            return Ok(runnable_class);
        }
```

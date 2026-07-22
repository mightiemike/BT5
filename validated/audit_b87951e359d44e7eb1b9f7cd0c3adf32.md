The code evidence confirms this divergence is real. Let me trace both paths precisely.

**`ExecutionStateReader::get_compiled_class_hash`** (lines 163–207):

1. Calls `get_class_definition_block_number(&class_hash)` → returns the block stored in `declared_classes_block_table`, which is the **original declaration block N**.
2. Calls `get_state_diff(block_number)` at block N.
3. Reads `class_hash_to_compiled_class_hash.get(&class_hash)` from that state diff → returns **V1 (Poseidon) hash**. [1](#0-0) 

**`append_state_diff`** at migration block N+M (lines 638–650):

- The `if not_declared` guard skips `declared_classes_block_table.insert` because the class is already declared → `declared_classes_block_table` still points to block N.
- But `write_compiled_class_hashes` **does** write `(class_hash, N+M) → V2_hash` into `compiled_class_hash_table`. [2](#0-1) [3](#0-2) 

**`ApolloReader::get_compiled_class_hash`** (lines 243–254):

Calls `get_compiled_class_hash_at(state_number, &class_hash)`, which is cursor-based: it does `lower_bound((class_hash, state_number+1))` then `prev()`, finding the **most recent** entry in `compiled_class_hash_table` — after migration, this is `(class_hash, N+M) → V2_hash`. [4](#0-3) [5](#0-4) 

---

### Title
`ExecutionStateReader::get_compiled_class_hash` returns stale V1 (Poseidon) hash after V1→V2 migration, diverging from `ApolloReader` — (`crates/apollo_rpc_execution/src/state_reader.rs`)

### Summary

After a class is migrated from V1 to V2 compiled class hash (migration block N+M appended to storage), `ExecutionStateReader::get_compiled_class_hash` continues to return the V1 (Poseidon) hash because it anchors its lookup to the original declaration block via `get_class_definition_block_number`, which is never updated on migration. `ApolloReader::get_compiled_class_hash` uses a cursor-based scan of `compiled_class_hash_table` and correctly returns the V2 (Blake) hash.

### Finding Description

`append_state_diff` maintains two separate tables:

- `declared_classes_block_table`: maps `class_hash → first_declaration_block`. The `if not_declared` guard at line 639 prevents this from being updated when the same class hash appears in a migration block's `class_hash_to_compiled_class_hash`. [6](#0-5) 

- `compiled_class_hash_table`: maps `(class_hash, block_number) → compiled_class_hash`. `write_compiled_class_hashes` unconditionally inserts a new row for every block that touches the class, including migration blocks. [3](#0-2) 

`ExecutionStateReader::get_compiled_class_hash` reads only from `declared_classes_block_table` (to get block N) and then from the serialized state diff blob at block N. It never consults `compiled_class_hash_table` at all. After migration block N+M, the state diff blob at block N still contains V1. [1](#0-0) 

`ApolloReader::get_compiled_class_hash` calls `get_compiled_class_hash_at`, which scans `compiled_class_hash_table` with a cursor and returns the most recent entry ≤ `state_number`. After migration, this is the V2 entry written at block N+M. [4](#0-3) 

### Impact Explanation

`ExecutionStateReader` is the state reader used by `apollo_rpc_execution` for `starknet_call`, `starknet_estimateFee`, and `starknet_simulateTransactions`. The blockifier's `should_migrate` logic (called inside `CasmHashMigrationData::from_state`) compares the stored compiled class hash against the V2 hash to decide whether migration gas should be charged. If `ExecutionStateReader` returns V1 for an already-migrated class, `should_migrate` concludes migration is still needed and adds migration gas to the fee estimate — an authoritative-looking wrong value returned to RPC callers. This falls under: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

### Likelihood Explanation

The divergence is triggered automatically once any migration block is committed to storage and a subsequent RPC fee estimation or simulation is made for a transaction that touches a migrated class. No special privileges are required; any user calling `starknet_estimateFee` or `starknet_simulateTransactions` after a migration block will observe the wrong fee.

### Recommendation

Replace the two-step `get_class_definition_block_number` + `get_state_diff` lookup in `ExecutionStateReader::get_compiled_class_hash` with the same cursor-based `get_compiled_class_hash_at` call used by `ApolloReader`, so it always returns the most recent compiled class hash up to `self.state_number`.

### Proof of Concept

1. Write a class with V1 hash at block 0 (`class_hash_to_compiled_class_hash: {C → V1}`).
2. Append a migration block 1 with `class_hash_to_compiled_class_hash: {C → V2}` (no new Sierra/CASM, just the hash update).
3. Construct an `ExecutionStateReader` with `state_number = right_after_block(1)`.
4. Call `exec_state_reader.get_compiled_class_hash(C)` → returns V1.
5. Construct an `ApolloReader` with `latest_block = 1`.
6. Call `apollo_reader.get_compiled_class_hash(C)` → returns V2.
7. Assert they differ — they will.

### Citations

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L174-207)
```rust
        let maybe_block_number = self
            .storage_reader
            .begin_ro_txn()
            .map_err(storage_err_to_state_err)?
            .get_state_reader()
            .map_err(storage_err_to_state_err)?
            .get_class_definition_block_number(&class_hash)
            .map_err(storage_err_to_state_err)?;

        // Cairo 0 classes (and undeclared classes) do not have a compiled class hash.
        // According to the trait, return the default value.
        let Some(block_number) = maybe_block_number else {
            return Ok(CompiledClassHash::default());
        };

        let state_diff = self
            .storage_reader
            .begin_ro_txn()
            .map_err(storage_err_to_state_err)?
            .get_state_diff(block_number)
            .map_err(storage_err_to_state_err)?
            .ok_or(StateError::StateReadError(format!(
                "Inner storage error. Missing state diff at block {block_number}."
            )))?;

        let compiled_class_hash = state_diff
            .class_hash_to_compiled_class_hash
            .get(&class_hash)
            .ok_or(StateError::StateReadError(format!(
                "Inner storage error. Missing class declaration at block {block_number}, class \
                 {class_hash}."
            )))?;

        Ok(*compiled_class_hash)
```

**File:** crates/apollo_storage/src/state/mod.rs (L638-650)
```rust
        for (class_hash, _) in &thin_state_diff.class_hash_to_compiled_class_hash {
            let not_declared = declared_classes_block_table.get(inner_txn, class_hash)?.is_none();
            if not_declared {
                declared_classes_block_table.insert(inner_txn, class_hash, &block_number)?;
            }
        }

        write_compiled_class_hashes(
            &thin_state_diff.class_hash_to_compiled_class_hash,
            inner_txn,
            block_number,
            &compiled_class_hash_table,
        )?;
```

**File:** crates/apollo_storage/src/state/mod.rs (L896-906)
```rust
fn write_compiled_class_hashes<'env>(
    compiled_class_hashes: &IndexMap<ClassHash, CompiledClassHash>,
    txn: &DbTransaction<'env, RW>,
    block_number: BlockNumber,
    compiled_class_hash_table: &'env CompiledClassHashTable<'env>,
) -> StorageResult<()> {
    for (class_hash, compiled_class_hash) in compiled_class_hashes {
        compiled_class_hash_table.insert(txn, &(*class_hash, block_number), compiled_class_hash)?;
    }
    Ok(())
}
```

**File:** crates/apollo_storage/src/state/mod.rs (L1161-1184)
```rust
fn get_compiled_class_hash_at<'env, Mode: TransactionKind>(
    first_irrelevant_block: BlockNumber,
    class_hash: &ClassHash,
    txn: &'env DbTransaction<'env, Mode>,
    compiled_class_hash_table: &'env CompiledClassHashTable<'env>,
) -> StorageResult<Option<CompiledClassHash>> {
    let db_key = (*class_hash, first_irrelevant_block);
    // Find the previous db item.
    let mut cursor = compiled_class_hash_table.cursor(txn)?;
    cursor.lower_bound(&db_key)?;
    let res = cursor.prev()?;
    match res {
        None => Ok(None),
        Some(((got_class_hash, _got_block_number), value)) => {
            if got_class_hash != *class_hash {
                // The previous item belongs to different class hash, which means there is no
                // previous state diff for this item.
                return Ok(None);
            };
            // The previous db item indeed belongs to this address and key.
            Ok(Some(value))
        }
    }
}
```

**File:** crates/apollo_state_reader/src/apollo_state.rs (L243-254)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        let state_number = StateNumber(self.latest_block);
        match self
            .reader()?
            .get_state_reader()
            .and_then(|sr| sr.get_compiled_class_hash_at(state_number, &class_hash))
        {
            Ok(Some(compiled_class_hash)) => Ok(compiled_class_hash),
            Ok(None) => Ok(CompiledClassHash::default()),
            Err(err) => Err(StateError::StateReadError(err.to_string())),
        }
    }
```

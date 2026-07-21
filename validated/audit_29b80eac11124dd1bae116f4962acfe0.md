### Title
`revert_state_diff` Unconditionally Deletes Sierra Class Definitions for CASM-Migrated Classes, Leaving Storage Permanently Inconsistent - (File: crates/apollo_storage/src/state/mod.rs)

### Summary
When `revert_state_diff` is called for a block that contains CASM hash migrations (v1→v2, enabled in v0.14.1+), the `delete_declared_classes` helper unconditionally deletes Sierra class definitions from `declared_classes_table` for every class appearing in `class_hash_to_compiled_class_hash` of the reverted state diff — including classes that were originally declared in an earlier block and only migrated in the reverted block. After the revert, `declared_classes_block_table` still holds the original declaration block number for those classes, but `declared_classes_table` no longer contains their Sierra definitions. Subsequent calls to `get_class_definition_at` for those classes return a `DBInconsistency` storage error, permanently breaking execution and RPC for any transaction that references them.

### Finding Description

**Write path — `append_state_diff`:**

When a class is first declared, `append_state_diff` writes to `declared_classes_block_table` only if the class has not been seen before:

```rust
for (class_hash, _) in &thin_state_diff.class_hash_to_compiled_class_hash {
    let not_declared = declared_classes_block_table.get(inner_txn, class_hash)?.is_none();
    if not_declared {
        declared_classes_block_table.insert(inner_txn, class_hash, &block_number)?;
    }
}
``` [1](#0-0) 

When CASM hash migration is enabled (`enable_casm_hash_migration = true`, active since v0.14.1), a class originally declared in block N can reappear in `class_hash_to_compiled_class_hash` in a later migration block M. In that case `append_state_diff` does **not** update `declared_classes_block_table` (the guard fires), but it **does** write a new entry to `compiled_class_hash_table` with the v2 hash. [2](#0-1) 

**Revert path — `revert_state_diff` → `delete_declared_classes`:**

`delete_declared_classes_block` correctly guards its deletion with a block-number equality check:

```rust
if class_block_entry == block_number {
    declared_classes_block_table.delete(txn, class_hash)?;
    ...
}
``` [3](#0-2) 

But `delete_declared_classes`, called immediately after, carries **no such guard**. It deletes from `declared_classes_table` for every class in `class_hash_to_compiled_class_hash`, regardless of which block originally declared it:

```rust
for class_hash in thin_state_diff.class_hash_to_compiled_class_hash.keys() {
    let Some(contract_class_location) = declared_classes_table.get(txn, class_hash)? else {
        continue;
    };
    ...
    declared_classes_table.delete(txn, class_hash)?;   // ← no block-number guard
}
``` [4](#0-3) 

Both calls are made unconditionally inside `revert_state_diff`: [5](#0-4) 

**Post-revert inconsistency:**

After reverting migration block M for class C (originally declared in block N):

| Table | Expected | Actual |
|---|---|---|
| `declared_classes_block_table[C]` | `N` (preserved) | `N` (preserved) ✓ |
| `declared_classes_table[C]` | Sierra definition present | **DELETED** ✗ |

`get_class_definition_at` first looks up `declared_classes_block_table` (finds block N), then looks up `declared_classes_table` (finds nothing), and returns a `DBInconsistency` storage error:

```rust
let Some(contract_class_location) =
    self.declared_classes_table.get(self.txn, class_hash)?
else {
    ...
    return Err(StorageError::DBInconsistency {
        msg: "Couldn't find class for a block that is before the class marker.".to_string(),
    });
};
``` [6](#0-5) 

### Impact Explanation

After a chain reorganization that reverts a block containing CASM hash migrations, the Sierra class definitions for all migrated classes are permanently deleted from `declared_classes_table`. Any subsequent transaction execution, RPC call (`starknet_getClass`, `starknet_call`, fee estimation, simulation), or blockifier execution that references those classes will fail with a storage inconsistency error, producing wrong execution results or hard errors. This matches the impact: **Wrong state/storage value or revert result from blockifier/syscall/execution logic for accepted input** (Critical) and **RPC execution, fee estimation, tracing, simulation returns an authoritative-looking wrong value** (High).

### Likelihood Explanation

Requires two conditions to hold simultaneously:
1. `enable_casm_hash_migration = true` — enabled by default in all versioned constants from v0.14.1 onward.
2. A chain reorganization that reverts a block in which at least one previously-declared class was migrated from v1 to v2 CASM hash.

Chain reorganizations are uncommon but are a normal part of sequencer operation. Once triggered, the corruption is permanent (no self-healing path exists in the storage layer).

### Recommendation

In `delete_declared_classes`, add the same block-number guard used by `delete_declared_classes_block`: only delete from `declared_classes_table` when the class was **first declared** in the block being reverted. Concretely, before deleting, check `declared_classes_block_table.get(txn, class_hash)? == Some(block_number)`. If the entry belongs to an earlier block, skip the deletion.

### Proof of Concept

1. Configure node with `enable_casm_hash_migration = true` (default in v0.14.1+).
2. Append block N declaring Sierra class C: `declared_classes_block_table[C] = N`, `declared_classes_table[C] = Sierra_C`.
3. Execute a transaction in block M (M > N) that causes class C to be executed; the bouncer records C for migration. `finalize_block` calls `set_compiled_class_hash_migration`, writing `compiled_class_hash_table[(C, M)] = v2_hash` into the state diff. `append_state_diff(M, ...)` writes `compiled_class_hash_table[(C, M)]` but does **not** update `declared_classes_block_table[C]` (already present).
4. Trigger a reorg: call `revert_state_diff(M)`.
   - `delete_declared_classes_block`: `class_block_entry = N ≠ M` → skips deletion. `declared_classes_block_table[C] = N` remains.
   - `delete_declared_classes`: finds `declared_classes_table[C]` → **deletes it**.
5. Call `get_class_definition_at(state_after_N, C)`:
   - Finds `declared_classes_block_table[C] = N` → proceeds.
   - Looks up `declared_classes_table[C]` → not found.
   - Returns `Err(StorageError::DBInconsistency { ... })`.

Any subsequent transaction or RPC call referencing class C now fails with a storage inconsistency error.

### Citations

**File:** crates/apollo_storage/src/state/mod.rs (L476-487)
```rust
        let Some(contract_class_location) =
            self.declared_classes_table.get(self.txn, class_hash)?
        else {
            if state_number
                .is_after(self.markers_table.get(self.txn, &MarkerKind::Class)?.unwrap_or_default())
            {
                return Ok(None);
            }
            return Err(StorageError::DBInconsistency {
                msg: "Couldn't find class for a block that is before the class marker.".to_string(),
            });
        };
```

**File:** crates/apollo_storage/src/state/mod.rs (L638-643)
```rust
        for (class_hash, _) in &thin_state_diff.class_hash_to_compiled_class_hash {
            let not_declared = declared_classes_block_table.get(inner_txn, class_hash)?.is_none();
            if not_declared {
                declared_classes_block_table.insert(inner_txn, class_hash, &block_number)?;
            }
        }
```

**File:** crates/apollo_storage/src/state/mod.rs (L733-744)
```rust
        let deleted_class_hashes = delete_declared_classes_block(
            inner_txn,
            &thin_state_diff,
            &declared_classes_block_table,
            block_number,
        )?;
        let deleted_classes = delete_declared_classes(
            inner_txn,
            &thin_state_diff,
            &declared_classes_table,
            self.file_handlers(),
        )?;
```

**File:** crates/apollo_storage/src/state/mod.rs (L940-943)
```rust
        if class_block_entry == block_number {
            declared_classes_block_table.delete(txn, class_hash)?;
            deleted_data.push(*class_hash);
        }
```

**File:** crates/apollo_storage/src/state/mod.rs (L955-963)
```rust
    for class_hash in thin_state_diff.class_hash_to_compiled_class_hash.keys() {
        let Some(contract_class_location) = declared_classes_table.get(txn, class_hash)? else {
            continue;
        };
        deleted_data.insert(
            *class_hash,
            file_handlers.get_contract_class_unchecked(contract_class_location)?,
        );
        declared_classes_table.delete(txn, class_hash)?;
```

**File:** crates/blockifier/resources/versioned_constants_diff_regression/0.14.0_0.14.1.txt (L1-2)
```text
~ /block_casm_hash_v1_declares: true
~ /enable_casm_hash_migration: true
```

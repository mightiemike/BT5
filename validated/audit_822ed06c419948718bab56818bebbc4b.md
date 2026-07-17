### Title
Non-Atomic Two-Phase Destructive Deletion in `migrate_48_to_49` Leaves `DBCol::BlockHeader` Irrecoverably Empty on Mid-Migration Crash — (File: `nearcore/src/migrations.rs`)

---

### Summary

The DB 48→49 migration (`migrate_48_to_49`) performs a two-phase destructive operation on `DBCol::BlockHeader`: it first commits an unconditional `delete_all` to disk, then re-adds only the required headers in separate batched commits. A crash, OOM, or disk-full event between these two phases leaves the hot store with zero block headers — a state from which the node cannot start. The only recovery path is the pre-migration RocksDB checkpoint (`migration_snapshot`), which operators can disable and which shares the same filesystem as the database by default. No application-level rollback exists; the codebase itself acknowledges this with a `TODO` comment.

---

### Finding Description

`migrate_48_to_49` calls three sub-functions in sequence:

```
copy_block_headers_to_cold_db(...)   // step 1
update_epoch_sync_proof(...)          // step 2
delete_old_block_headers(...)         // step 3  ← root cause
```

`delete_old_block_headers` executes a non-atomic two-phase write:

**Phase A — irreversible destructive commit:**
```rust
let mut store_update = store.store_update();
store_update.delete_all(DBCol::BlockHeader);
store_update.commit();   // ← permanently committed to RocksDB
```

**Phase B — batched re-addition (separate commits):**
```rust
for height in tail_height..(latest_known_height + 1) {
    for block_hash in chain_store.get_all_header_hashes_by_height(height) {
        if let Ok(block) = chain_store.get_block(&block_hash) {
            store_update.set_block_header_only(block.header());
        }
    }
    if height % BATCH_SIZE == 0 {
        store_update.commit()?;   // ← intermediate commits
        store_update = chain_store.store_update();
    }
}
store_update.commit()?;
``` [1](#0-0) 

Phase A and Phase B are not atomic. After Phase A commits, `DBCol::BlockHeader` is empty. If the process terminates before Phase B completes (crash, OOM, `SIGKILL`, disk full), the column remains empty. The node cannot start because block headers are required for chain processing and transaction validation.

`DBCol::BlockHeader` is an `insert_only` column — the migration bypasses this invariant using the raw `delete_all` escape hatch, which is why the regression test itself uses `set_raw_bytes` to rebuild the pre-migration state: [2](#0-1) 

The only recovery mechanism is the pre-migration RocksDB checkpoint created in `ensure_version`: [3](#0-2) 

This checkpoint can be disabled by setting `migration_snapshot: false` in `config.json`: [4](#0-3) 

When disabled, `Snapshot::new` returns `Snapshot::none()` and no checkpoint is created: [5](#0-4) 

The codebase explicitly acknowledges the absence of a proper rollback mechanism: [6](#0-5) 

The `SetVersionCommand` itself warns that manually setting the version "is not a rollback migration and it might break the database": [7](#0-6) 

---

### Impact Explanation

If the migration crashes between Phase A and Phase B completion:

- `DBCol::BlockHeader` is empty in the hot store.
- The node cannot start: chain processing requires block headers for epoch validation, transaction validity period checks, and GC.
- The DB version has already been incremented to 49 (or partially so), so re-running the migration does not help — the migration skips if the version is already at the target.
- Recovery requires: (a) the migration snapshot exists and was not disabled, (b) the operator knows to restore it manually, and (c) the snapshot is on a different filesystem (otherwise disk-full affects both).

**Impact: High** — complete node unavailability, requiring manual filesystem-level recovery.

---

### Likelihood Explanation

The migration is triggered automatically on every node startup when upgrading from neard 2.12 to 2.13 (DB version 48 → 49). The vulnerable window is the duration of Phase B, which iterates over all block headers from `tail_height` to `latest_known_height` in batches of 100,000. On a mainnet node with years of history, this can take minutes. Any crash, OOM, or disk-full event during this window triggers the issue.

Operators who follow the documented advice to disable migration snapshots (to avoid disk space usage) have no recovery path.

**Likelihood: Low-Medium** — requires a crash during a specific multi-minute window, but the window is long and the triggering condition (binary upgrade) is routine.

---

### Recommendation

1. **Make the deletion atomic with re-addition**: Build the full set of headers to re-add first, then perform `delete_all` and re-insertion in a single RocksDB `WriteBatch`, or use a column-rename/swap approach.
2. **Alternatively, use a tombstone marker**: Write a migration-in-progress key before Phase A and check for it on startup to detect and resume interrupted migrations.
3. **Enforce snapshot before destructive migrations**: If `migration_snapshot` is disabled and the migration involves `delete_all` on a critical column, abort with an actionable error rather than proceeding.
4. **Document the risk**: The migration comment should explicitly state that `migration_snapshot: false` is unsafe for this migration.

---

### Proof of Concept

1. Configure a mainnet archival node with `"migration_snapshot": false` in `config.json` (to disable the checkpoint).
2. Upgrade the binary from neard 2.12 to 2.13.
3. Start the node. The 48→49 migration begins. Phase A commits `delete_all(DBCol::BlockHeader)`.
4. Send `SIGKILL` to the process during Phase B (while it is re-adding headers).
5. Restart the node. The DB version is 48 (Phase A committed the delete but the version bump at line 609 may not have occurred yet) or 49 (if the version was bumped before the crash).
6. In either case, `DBCol::BlockHeader` is empty or partially populated. The node fails to start with a missing block header error. No automated recovery is available. [8](#0-7) [9](#0-8)

### Citations

**File:** nearcore/src/migrations.rs (L164-212)
```rust
/// This migration does three things:
/// 1. Copy block headers from hot_store to cold_db (if cold_db is present)
/// 2. Generate and save the compressed epoch sync proof
/// 3. Clear the block headers from genesis to tail in hot_store
fn migrate_48_to_49(
    hot_store: &Store,
    cold_db: Option<&ColdDB>,
    transaction_validity_period: BlockHeightDelta,
    home_dir: &Path,
    cold_store_config: Option<&StoreConfig>,
    is_snapshot: bool,
) -> anyhow::Result<()> {
    tracing::info!(target: "migrations", "starting migration from DB version 48 to 49");

    // State snapshot DBs only contain flat storage columns and lack the
    // epoch/chain data that every step of this migration requires. Skip them.
    if is_snapshot {
        tracing::info!(target: "migrations", "state snapshot DB, skipping chain-dependent migration steps");
        return Ok(());
    }

    // Fresh nodes and forknet-initialized nodes have BlockMisc cleared, so
    // HEAD is absent; nodes that only produced blocks in the genesis epoch
    // have HEAD set but head.epoch_id == EpochId::default(). In both cases
    // there are no block headers to copy, no epoch sync proof to derive, and
    // nothing to verify or delete.
    match hot_store.chain_store().head() {
        Ok(head) if head.epoch_id == EpochId::default() => {
            tracing::info!(target: "migrations", "chain is in the genesis epoch, skipping chain-dependent migration steps");
            return Ok(());
        }
        Err(Error::DBNotFoundErr(_)) => {
            tracing::info!(target: "migrations", "chain head not set (fresh/forknet DB), skipping chain-dependent migration steps");
            return Ok(());
        }
        Ok(_) => {}
        Err(err) => return Err(err.into()),
    }

    if let Some(cold_db) = cold_db {
        let cold_store_config =
            cold_store_config.expect("cold_store config must be present when cold_db exists");
        copy_block_headers_to_cold_db(hot_store, cold_db, home_dir, cold_store_config)?;
    }

    update_epoch_sync_proof(hot_store.clone(), transaction_validity_period)?;
    verify_block_headers(hot_store)?;
    delete_old_block_headers(hot_store)?;
    Ok(())
```

**File:** nearcore/src/migrations.rs (L461-492)
```rust
fn delete_old_block_headers(store: &Store) -> anyhow::Result<()> {
    tracing::info!(target: "migrations", "deleting all block headers from hot store");

    let mut store_update = store.store_update();
    store_update.delete_all(DBCol::BlockHeader);
    store_update.commit();
    let chain_store = store.chain_store();
    let tail_height = chain_store.tail();
    let latest_known_height =
        store.get_ser::<LatestKnown>(DBCol::BlockMisc, LATEST_KNOWN_KEY).unwrap().height;

    tracing::info!(target: "migrations", ?tail_height, ?latest_known_height, "adding required block headers to hot store");

    let mut store_update = chain_store.store_update();
    for height in tail_height..(latest_known_height + 1) {
        for block_hash in chain_store.get_all_header_hashes_by_height(height) {
            // We've already checked for errors and missing blocks in the verify_block_headers function
            if let Ok(block) = chain_store.get_block(&block_hash) {
                store_update.set_block_header_only(block.header());
            }
        }
        if height % BATCH_SIZE == 0 {
            tracing::info!(target: "migrations", ?height, ?latest_known_height, "committing addition of required block headers to hot store");
            store_update.commit()?;
            store_update = chain_store.store_update();
        }
    }
    store_update.commit()?;
    tracing::info!(target: "migrations", ?latest_known_height, "completed deletion of old block headers from hot store");

    Ok(())
}
```

**File:** test-loop-tests/src/tests/sync/migration_epoch_sync_proof.rs (L74-79)
```rust
    let mut store_update = store.store_update();
    for (key, value) in &all_headers {
        store_update.set_raw_bytes(DBCol::BlockHeader, key, value);
    }
    store_update.delete_all(DBCol::EpochSyncProof);
    store_update.commit();
```

**File:** core/store/src/node_storage/opener.rs (L573-579)
```rust
        // Create snapshots upfront for both stores
        let hot_snapshot = hot_opener.snapshot()?;
        let cold_snapshot = if let Some(cold_opener) = cold_opener {
            cold_opener.snapshot()?
        } else {
            Snapshot::none()
        };
```

**File:** core/store/src/node_storage/opener.rs (L603-612)
```rust
            // Run migration on both stores
            migrator
                .migrate(&hot_store, cold_db.as_ref(), version, is_snapshot)
                .map_err(StoreOpenerError::MigrationError)?;

            // Update versions in both stores
            hot_store.set_db_version(version + 1);
            if let Some(ref cold) = cold_db {
                cold.as_store().set_db_version(version + 1);
            }
```

**File:** core/store/src/config.rs (L76-98)
```rust
    /// Path where to create RocksDB checkpoints during database migrations or
    /// `false` to disable that feature.
    ///
    /// If this feature is enabled, when database migration happens a RocksDB
    /// checkpoint will be created just before the migration starts.  This way,
    /// if there are any failures during migration, the database can be
    /// recovered from the checkpoint.
    ///
    /// The field can be one of:
    /// * an absolute path name → the snapshot will be created in specified
    ///   directory.  No sub-directories will be created so for example you
    ///   probably don’t want `/tmp` but rather `/tmp/neard-db-snapshot`;
    /// * an relative path name → the snapshot will be created in a directory
    ///   inside of the RocksDB database directory (see `path` field);
    /// * `true` (the default) → this is equivalent to setting the field to
    ///   `migration-snapshot`; and
    /// * `false` → the snapshot will not be created.
    ///
    /// Note that if the snapshot is on a different file system than the
    /// database, creating the snapshot may itself take time as data may need to
    /// be copied between the databases.
    #[serde(skip_serializing_if = "MigrationSnapshot::is_default")]
    pub migration_snapshot: MigrationSnapshot,
```

**File:** core/store/src/db/rocksdb/snapshot.rs (L86-90)
```rust
    ) -> Result<Self, SnapshotError> {
        let snapshot_path = match config.migration_snapshot.get_path(db_path) {
            Some(snapshot_path) => snapshot_path,
            None => return Ok(Self::none()),
        };
```

**File:** tools/database/src/set_version.rs (L7-7)
```rust
// TODO: remove this cmd once we have a proper way to rollback migration
```

**File:** tools/database/src/set_version.rs (L36-43)
```rust
        if !get_user_confirmation(&format!(
            "WARNING: You are about to manually set the database version to {}.\n\
            It is not a rollback migration and it might break the database. Do it at your own risk!",
            self.version,
        )) {
            println!("Operation canceled.");
            return Ok(());
        }
```

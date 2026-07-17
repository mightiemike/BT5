### Title
Silent Failure of Cold-Store DB Write in Migration 47→48 Discards Recovered Trie Data - (File: nearcore/src/migrations.rs)

### Summary

`migrate_47_to_48` calls `cold_db.write(transaction)` without checking or propagating the return value. If the write fails, the function returns `Ok(())`, the migration framework advances the DB version to 48, and the node continues as if the recovery succeeded — but the trie insertions are never committed to the cold store.

### Finding Description

`migrate_47_to_48` is the production migration that recovers shard-1 trie data lost during the March 2024 Resharding V2 event on mainnet. After computing the trie changes and building a `DBTransaction` of insertions, the write is issued without any error check:

```rust
tracing::info!(target: "migrations", "Writing changes to the database");
cold_db.write(transaction);   // ← return value silently discarded
Ok(())
``` [1](#0-0) 

Every other fallible operation in the same function uses `?` to propagate errors:

```rust
let trie_changes = recover_shard_1_at_block_height_115185108(
    &tries,
    &mut cold_store.trie_store().store_update(),
)?;
``` [2](#0-1) 

`ColdDB::write` (backed by RocksDB) returns a `Result`. Discarding it means any I/O error, RocksDB write-stall, or disk-full condition is swallowed. The migration framework then records the DB version as 48 and never re-runs the migration, leaving the cold store permanently missing the recovered trie nodes.

### Impact Explanation

An archival node that runs this migration on a disk under pressure or with a transient RocksDB error will silently skip the data recovery. The cold store will be at DB version 48 (migration "complete") but will be missing the trie insertions for `ShardUId::new(3, ShardId::new(1))` at block height 115185108. Any subsequent historical state queries against that shard/height will return incorrect or missing data with no error surfaced to the caller. Because the migration is marked complete, the node will never retry it. [3](#0-2) 

### Likelihood Explanation

The migration runs automatically at node startup whenever the stored DB version is 47 and the node is a mainnet archival node with a cold store. RocksDB write failures are realistic under disk pressure, filesystem errors, or resource limits. The silent discard means operators have no indication of failure beyond a missing log line — the node starts normally and serves stale data.

### Recommendation

Propagate the write result with `?`:

```rust
tracing::info!(target: "migrations", "Writing changes to the database");
cold_db.write(transaction)?;
Ok(())
```

This matches the error-handling pattern used for every other fallible call in the same function and ensures the migration framework does not advance the DB version on a failed write.

### Proof of Concept

1. Run a mainnet archival node with DB version 47 and a cold store on a filesystem that returns an I/O error on the next RocksDB write (e.g., via fault injection or a full disk).
2. Node startup calls `migrate_47_to_48`.
3. `cold_db.write(transaction)` fails; the error is discarded.
4. The function returns `Ok(())`.
5. The migration framework writes DB version 48.
6. The node starts normally. Querying historical state for shard 1 at block 115185108 returns missing/incorrect trie data.
7. Restarting the node does not re-run the migration because the DB version is already 48. [4](#0-3)

### Citations

**File:** nearcore/src/migrations.rs (L86-129)
```rust
fn migrate_47_to_48(
    cold_db: Option<&ColdDB>,
    genesis_config: &GenesisConfig,
    store_config: &StoreConfig,
) -> anyhow::Result<()> {
    tracing::info!(target: "migrations", "starting migration from DB version 47 to 48");

    let Some(cold_db) = cold_db else {
        tracing::info!(target: "migrations", "skipping migration 47->48 for hot store only");
        return Ok(());
    };

    // Current migration is targeted only for mainnet
    if genesis_config.chain_id != MAINNET {
        tracing::info!(target: "migrations", chain_id = ?genesis_config.chain_id, "skipping migration 47->48");
        return Ok(());
    }

    tracing::info!(target: "migrations", "starting migration 47->48 for cold store");

    let cold_store = cold_db.as_store();
    let tries = ShardTries::new(
        cold_store.trie_store(),
        TrieConfig::from_store_config(store_config),
        FlatStorageManager::new(cold_store.flat_store()),
        StateSnapshotConfig::Disabled,
    );

    // We ignore the store update, as we need to construct a transaction manually from trie changes.
    let trie_changes = recover_shard_1_at_block_height_115185108(
        &tries,
        &mut cold_store.trie_store().store_update(),
    )?;
    let mut transaction = DBTransaction::new();
    let child_shard_uid = ShardUId::new(3, ShardId::new(1));
    for op in trie_changes.insertions() {
        let key = join_two_keys(&child_shard_uid.to_bytes(), op.hash().as_bytes());
        let value = op.payload().to_vec();
        rc_aware_set(&mut transaction, DBCol::State, key, value);
    }
    tracing::info!(target: "migrations", "Writing changes to the database");
    cold_db.write(transaction);
    Ok(())
}
```

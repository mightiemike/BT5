Looking at the actual code paths carefully to determine if the divergence is real and reachable.

### Title
Pending `starknet_getStateUpdate` omits `migrated_compiled_classes` from `declared_classes`, diverging from accepted-block view — (`crates/apollo_rpc/src/v0_8/state.rs`)

### Summary

The `From<ClientStateDiff> for ThinStateDiff` conversion used by the pending-block RPC path silently drops `migrated_compiled_classes` entries. The accepted-block path reads a `starknet_api::state::ThinStateDiff` from storage whose `class_hash_to_compiled_class_hash` field was built by merging both `declared_classes` and `migrated_compiled_classes`. The result is that `starknet_getStateUpdate` returns a `declared_classes` array of different length for the pending vs accepted view of the same logical block.

### Finding Description

**Pending path** (`BlockId::Tag(Tag::Pending)` in `get_state_update`): [1](#0-0) 

The call `state_update.state_diff.into()` dispatches to `From<ClientStateDiff> for ThinStateDiff`: [2](#0-1) 

This conversion maps only `diff.declared_classes` into `declared_classes`. The `diff.migrated_compiled_classes` field — which is present on `ClientStateDiff` — is never read: [3](#0-2) 

**Accepted path** (`get_state_diff` from storage): [4](#0-3) 

The stored `starknet_api::state::ThinStateDiff` was written with `class_hash_to_compiled_class_hash` built by chaining both `declared_classes` and `migrated_compiled_classes`: [5](#0-4) 

The accepted-block RPC converter then maps this merged field directly to `declared_classes`: [6](#0-5) 

**Concrete divergent value**: for a block with `migrated_compiled_classes = [{class_hash: X, compiled_class_hash: Y}]` and `declared_classes = []`:
- Pending view → `declared_classes: []` (length 0)
- Accepted view → `declared_classes: [{class_hash: X, compiled_class_hash: Y}]` (length 1)

The `migrated_compiled_classes` type is a type alias for the same struct as `DeclaredClassHashEntry`: [7](#0-6) 

The field is already exercised in production test fixtures with non-empty values: [8](#0-7) 

### Impact Explanation

Any unprivileged caller of `starknet_getStateUpdate` with `block_id = pending` receives a `declared_classes` array that is missing all migrated compiled class hash entries. The same caller querying the same block after acceptance receives a longer, correct array. This is a High-severity RPC inconsistency: the pending view returns an authoritative-looking wrong value for `declared_classes`, which clients use to track class hash → compiled class hash bindings. Clients that cache or act on the pending state update (e.g., for fee estimation, class resolution, or state tracking) will hold stale/incomplete data that silently becomes inconsistent once the block is accepted.

### Likelihood Explanation

`migrated_compiled_classes` is a live protocol feature (CASM hash v1→v2 migration, gated by `enable_casm_hash_migration` in `VersionedConstants`). Any block produced after the migration flag is enabled that executes a class declared with a v1 hash will carry non-empty `migrated_compiled_classes` in its state diff. The feeder gateway already serializes this field and the client already deserializes it. No attacker action is required — the divergence is structural and fires on every such block.

### Recommendation

In `From<ClientStateDiff> for ThinStateDiff` (`crates/apollo_rpc/src/v0_8/state.rs`, lines 72–78), extend the `declared_classes` mapping to also chain `diff.migrated_compiled_classes`:

```rust
declared_classes: diff
    .declared_classes
    .into_iter()
    .map(|ClientDeclaredClassHashEntry { class_hash, compiled_class_hash }| {
        ClassHashes { class_hash, compiled_class_hash }
    })
    .chain(diff.migrated_compiled_classes.into_iter().map(
        |MigratedCompiledClassHashEntry { class_hash, compiled_class_hash }| {
            ClassHashes { class_hash, compiled_class_hash }
        },
    ))
    .collect(),
```

This mirrors exactly what `ThinStateDiff::from_state_diff` does on the storage write path.

### Proof of Concept

The existing test at `crates/apollo_rpc/src/v0_8/api/test.rs` line 2605 already sets `migrated_compiled_classes: vec![]` explicitly, which masks the bug. A minimal reproduction:

1. In `get_state_update` test, set `migrated_compiled_classes` to a non-empty vec (e.g., one entry with `class_hash = 0xABC`, `compiled_class_hash = 0xDEF`) while keeping `declared_classes` empty.
2. Also write a storage block whose `class_hash_to_compiled_class_hash` contains the same entry (simulating what `ThinStateDiff::from_state_diff` would produce).
3. Call `starknet_getStateUpdate` with `pending` → `declared_classes` is empty.
4. Call `starknet_getStateUpdate` with the accepted block number → `declared_classes` has one entry.
5. Assert equality → test fails, confirming the divergence. [9](#0-8)

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L487-492)
```rust
        if let BlockId::Tag(Tag::Pending) = block_id {
            let state_update = read_pending_data(&self.pending_data, &txn).await?.state_update;
            return Ok(StateUpdate::PendingStateUpdate(PendingStateUpdate {
                old_root: state_update.old_root,
                state_diff: state_update.state_diff.into(),
            }));
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L511-520)
```rust
        let mut thin_state_diff = txn
            .get_state_diff(block_number)
            .map_err(internal_server_error)?
            .ok_or_else(|| ErrorObjectOwned::from(BLOCK_NOT_FOUND))?;
        // Remove empty storage diffs. Some blocks contain empty storage diffs that must be kept for
        // the computation of state diff commitment.
        thin_state_diff.storage_diffs.retain(|_k, v| !v.is_empty());

        let state_diff =
            self.convert_thin_state_diff(thin_state_diff, block_id, block_number).await?;
```

**File:** crates/apollo_rpc/src/v0_8/state.rs (L53-92)
```rust
impl From<ClientStateDiff> for ThinStateDiff {
    fn from(diff: ClientStateDiff) -> Self {
        Self {
            deployed_contracts: Vec::from_iter(diff.deployed_contracts.into_iter().map(
                |ClientDeployedContract { address, class_hash }| DeployedContract {
                    address,
                    class_hash,
                },
            )),
            storage_diffs: Vec::from_iter(diff.storage_diffs.into_iter().map(
                |(address, entries)| {
                    let storage_entries = Vec::from_iter(
                        entries
                            .into_iter()
                            .map(|ClientStorageEntry { key, value }| StorageEntry { key, value }),
                    );
                    StorageDiff { address, storage_entries }
                },
            )),
            declared_classes: diff
                .declared_classes
                .into_iter()
                .map(|ClientDeclaredClassHashEntry { class_hash, compiled_class_hash }| {
                    ClassHashes { class_hash, compiled_class_hash }
                })
                .collect(),
            deprecated_declared_classes: diff.old_declared_contracts,
            nonces: Vec::from_iter(
                diff.nonces
                    .into_iter()
                    .map(|(contract_address, nonce)| ContractNonce { contract_address, nonce }),
            ),
            replaced_classes: Vec::from_iter(diff.replaced_classes.into_iter().map(
                |ClientReplacedClass { address: contract_address, class_hash }| ReplacedClass {
                    contract_address,
                    class_hash,
                },
            )),
        }
    }
```

**File:** crates/apollo_rpc/src/v0_8/state.rs (L130-137)
```rust
            declared_classes: diff
                .class_hash_to_compiled_class_hash
                .into_iter()
                .map(|(class_hash, compiled_class_hash)| ClassHashes {
                    class_hash,
                    compiled_class_hash,
                })
                .collect(),
```

**File:** crates/apollo_starknet_client/src/reader/objects/state.rs (L34-40)
```rust
    // TODO(Aviv): Remove this field after we upgrade to 0.14.1.
    #[serde(default)]
    pub migrated_compiled_classes: Vec<MigratedCompiledClassHashEntry>,
    pub old_declared_contracts: Vec<ClassHash>,
    pub nonces: IndexMap<ContractAddress, Nonce>,
    pub replaced_classes: Vec<ReplacedClass>,
}
```

**File:** crates/starknet_api/src/state.rs (L85-94)
```rust
                class_hash_to_compiled_class_hash: diff
                    .declared_classes
                    .iter()
                    .map(|(class_hash, (compiled_hash, _class))| (*class_hash, *compiled_hash))
                    .chain(
                        diff.migrated_compiled_classes
                            .iter()
                            .map(|(class_hash, compiled_hash)| (*class_hash, *compiled_hash)),
                    )
                    .collect(),
```

**File:** crates/papyrus_common/src/state.rs (L29-30)
```rust
pub type DeclaredClassHashEntry = ClassHashToCompiledClassHashEntry;
pub type MigratedCompiledClassHashEntry = ClassHashToCompiledClassHashEntry;
```

**File:** crates/apollo_rpc/src/v0_8/execution_test.rs (L1627-1630)
```rust
                migrated_compiled_classes: vec![MigratedCompiledClassHashEntry {
                    class_hash: class_hash1,
                    compiled_class_hash,
                }],
```

**File:** crates/apollo_rpc/src/v0_8/api/test.rs (L2598-2606)
```rust
            declared_classes: expected_state_diff
                .declared_classes
                .into_iter()
                .map(|ClassHashes { class_hash, compiled_class_hash }| {
                    ClientDeclaredClassHashEntry { class_hash, compiled_class_hash }
                })
                .collect(),
            migrated_compiled_classes: vec![],
            old_declared_contracts: expected_state_diff.deprecated_declared_classes,
```

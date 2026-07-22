### Title
Class-hash key dropped at `finalize_block` migration serialization boundary causes ambiguous OS-input reconstruction — (`echonet/os_input_builder.py`, `crates/blockifier/src/blockifier/transaction_executor.rs`)

---

### Summary

`finalize_block` serializes compiled-class-hash migration data by calling `.into_values()` on a `HashMap<ClassHash, (casm_v2, casm_v1)>`, silently dropping every `ClassHash` key. The OS input builder (`echonet/os_input_builder.py`) must then reconstruct the class hash via a reverse lookup of `casm_v1` in `initial_reads.compiled_class_hashes`. If two distinct class hashes share the same `casm_v1` hash — possible when two different Sierra classes compile to identical CASM bytecode — the reverse lookup is ambiguous, the OS input builder raises `OsInputBuildError`, and proof generation for that block fails entirely. The class hash, the canonical identifier of the migration, is irreversibly lost at the serialization boundary.

---

### Finding Description

**Step 1 — Key is dropped in `finalize_block`.** [1](#0-0) 

At line 312, `class_hashes_to_migrate` is a `HashMap<ClassHash, CompiledClassHashV2ToV1>`. Calling `.into_values().collect()` discards every `ClassHash` key and produces only `Vec<(casm_v2, casm_v1)>`:

```rust
compiled_class_hashes_for_migration: class_hashes_to_migrate.into_values().collect(),
```

The type alias confirms the key is gone: [2](#0-1) 

```rust
pub type CompiledClassHashV2ToV1 = (CompiledClassHash, CompiledClassHash);
pub type CompiledClassHashesForMigration = Vec<CompiledClassHashV2ToV1>;
```

**Step 2 — OS input builder must reconstruct the class hash via reverse lookup.**

The `_class_hashes_to_migrate` function in `echonet/os_input_builder.py` explicitly documents this loss and attempts recovery: [3](#0-2) 

```python
def _class_hashes_to_migrate(blob):
    """
    The blob carries `(casm_v2, casm_v1)` pairs whose class-hash keys were
    dropped by blockifier's `finalize_block` (`.into_values()`), while the OS
    expects `(class_hash, casm_v2)`. Recover each class hash by reverse lookup
    of the v1 hash in `initial_reads.compiled_class_hashes` ...
    """
    ...
    for casm_v2, casm_v1 in pairs:
        candidate_class_hashes = class_hashes_by_casm_v1.get(casm_v1, [])
        if len(candidate_class_hashes) != 1:
            raise OsInputBuildError(...)
        migration_pairs.append([candidate_class_hashes[0], casm_v2])
```

The recovery assumes the mapping `casm_v1 → class_hash` is injective. It is not guaranteed to be so.

**Step 3 — The injectivity assumption is broken.**

The `should_migrate` function selects classes for migration based on whether their state-stored compiled class hash differs from `casm_v2`: [4](#0-3) 

The `casm_v1` hash is a Poseidon hash of the CASM bytecode. Two distinct Sierra classes (differing in ABI, metadata, or Sierra-level constructs that do not affect compilation) can produce identical CASM and therefore identical `casm_v1`. Their `class_hash` values differ (because `class_hash` covers the full Sierra program including ABI), but their `casm_v1` values are equal.

**Step 4 — The migration data in `BlockExecutionSummary` and `CentralObjects` propagates the keyless form.** [5](#0-4) [6](#0-5) 

The `compiled_class_hashes_for_migration` field flows from `BlockExecutionSummary` → `CentralObjects` → the blob JSON consumed by `echonet/os_input_builder.py`, carrying only `(casm_v2, casm_v1)` pairs with no class hash at any stage.

**Step 5 — The OS Cairo code confirms the OS expects `(class_hash, casm_v2)`, not `(casm_v2, casm_v1)`.** [7](#0-6) 

`migrate_classes_to_v2_casm_hash` in the OS Cairo program operates on `(class_hash, casm_v2)` pairs. The OS input builder must supply this form, which it cannot do correctly when the reverse lookup is ambiguous.

---

### Impact Explanation

When two class hashes share the same `casm_v1` hash and both are executed in the same block during the migration window, `_class_hashes_to_migrate` raises `OsInputBuildError` and the OS input cannot be constructed. Proof generation for that block fails. The OS cannot correctly identify which class hash to associate with the migration, matching the impact category: **Critical — wrong compiled class hash selected for execution** (the OS input builder cannot produce the correct `(class_hash, casm_v2)` pair required by `migrate_classes_to_v2_casm_hash`).

---

### Likelihood Explanation

**Low.** Two conditions must hold simultaneously:
1. Two distinct Sierra classes with identical CASM bytecode (same `casm_v1`) must both have been declared before `block_casm_hash_v1_declares = true` (introduced in v0.14.1).
2. Both must be executed in the same block during the migration window.

The `block_casm_hash_v1_declares` flag prevents new `casm_v1` declarations going forward, so only pre-existing classes are affected. However, the condition is not impossible: Sierra classes that differ only in ABI or non-compiled metadata can produce identical CASM.

---

### Recommendation

**Short term:** Preserve the `ClassHash` key throughout the migration data pipeline. Change `CompiledClassHashesForMigration` from `Vec<(casm_v2, casm_v1)>` to `Vec<(ClassHash, casm_v2, casm_v1)>` (or keep it as `HashMap<ClassHash, CompiledClassHashV2ToV1>`), and propagate the class hash through `BlockExecutionSummary`, `CentralObjects`, and the blob JSON. This eliminates the reverse lookup in `_class_hashes_to_migrate` entirely.

**Long term:** Audit all serialization boundaries where canonical identifiers (class hash, transaction hash, contract address) are dropped in favor of derived values, and enforce that the canonical identifier is always preserved across the blockifier → batcher → OS input pipeline.

---

### Proof of Concept

1. Before v0.14.1, declare Sierra class **A** (with ABI_A, logic L) → `class_hash_A`, `casm_v1_X`.
2. Before v0.14.1, declare Sierra class **B** (with ABI_B, same logic L) → `class_hash_B`, `casm_v1_X` (identical CASM, same `casm_v1`).
3. After v0.14.1 (`enable_casm_hash_migration = true`), execute a transaction that touches both class A and class B in the same block.
4. `should_migrate` returns `Some` for both: `class_hash_A → (casm_v2_X, casm_v1_X)` and `class_hash_B → (casm_v2_X, casm_v1_X)`.
5. `finalize_block` calls `.into_values().collect()` → `compiled_class_hashes_for_migration = [(casm_v2_X, casm_v1_X), (casm_v2_X, casm_v1_X)]`.
6. `_class_hashes_to_migrate` iterates: for `(casm_v2_X, casm_v1_X)`, `class_hashes_by_casm_v1[casm_v1_X] = [class_hash_A, class_hash_B]` → `len == 2 != 1` → `OsInputBuildError` raised.
7. OS input construction fails; proof generation for the block is impossible.

### Citations

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L51-52)
```rust
pub type CompiledClassHashV2ToV1 = (CompiledClassHash, CompiledClassHash);
pub type CompiledClassHashesForMigration = Vec<CompiledClassHashV2ToV1>;
```

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L54-65)
```rust
#[cfg_attr(test, derive(PartialEq))]
#[derive(Debug)]
pub struct BlockExecutionSummary {
    pub state_diff: CommitmentStateDiff,
    pub compressed_state_diff: Option<CommitmentStateDiff>,
    #[cfg(feature = "os_input")]
    pub initial_reads: StateMaps,
    pub bouncer_weights: BouncerWeights,
    pub casm_hash_computation_data_sierra_gas: CasmHashComputationData,
    pub casm_hash_computation_data_proving_gas: CasmHashComputationData,
    pub compiled_class_hashes_for_migration: CompiledClassHashesForMigration,
    pub block_info: BlockInfo,
```

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L258-312)
```rust
    let class_hashes_to_migrate = mem::take(bouncer.get_mut_class_hashes_to_migrate());
    #[cfg(any(test, feature = "testing"))]
    if !class_hashes_to_migrate.is_empty() {
        log::info!(
            "Class hashes to migrate (key = class_hash, value = (compiled_class_hash_v2, \
             compiled_class_hash_v1)): {class_hashes_to_migrate:#?}"
        );
    }

    if !block_context.versioned_constants.enable_casm_hash_migration {
        assert!(
            class_hashes_to_migrate.is_empty(),
            "Class hashes to migrate should be empty when migration is disabled"
        );
    }
    block_state.set_compiled_class_hash_migration(&class_hashes_to_migrate)?;

    let state_diff = block_state.to_state_diff()?.state_maps;

    #[cfg(feature = "os_input")]
    let initial_reads = block_state.get_os_initial_reads()?;

    let compressed_state_diff = if block_context.versioned_constants.enable_stateful_compression {
        Some(compress(&state_diff, block_state, alias_contract_address)?.into())
    } else {
        None
    };

    // Take CasmHashComputationData from bouncer,
    // and verify that class hashes are the same.
    let casm_hash_computation_data_sierra_gas =
        mem::take(bouncer.get_mut_casm_hash_computation_data_sierra_gas());
    let casm_hash_computation_data_proving_gas =
        mem::take(bouncer.get_mut_casm_hash_computation_data_proving_gas());

    assert_eq!(
        casm_hash_computation_data_sierra_gas
            .class_hash_to_casm_hash_computation_gas
            .keys()
            .collect::<std::collections::HashSet<_>>(),
        casm_hash_computation_data_proving_gas
            .class_hash_to_casm_hash_computation_gas
            .keys()
            .collect::<std::collections::HashSet<_>>()
    );

    Ok(BlockExecutionSummary {
        state_diff: state_diff.into(),
        compressed_state_diff,
        #[cfg(feature = "os_input")]
        initial_reads,
        bouncer_weights: *bouncer.get_bouncer_weights(),
        casm_hash_computation_data_sierra_gas,
        casm_hash_computation_data_proving_gas,
        compiled_class_hashes_for_migration: class_hashes_to_migrate.into_values().collect(),
```

**File:** echonet/os_input_builder.py (L341-366)
```python
def _class_hashes_to_migrate(blob: JsonObject) -> List[List[str]]:
    """
    The blob carries `(casm_v2, casm_v1)` pairs whose class-hash keys were
    dropped by blockifier's `finalize_block` (`.into_values()`), while the OS
    expects `(class_hash, casm_v2)`. Recover each class hash by reverse lookup
    of the v1 hash in `initial_reads.compiled_class_hashes` — a migrating
    class's state entry still holds v1 (that is `should_migrate`'s condition).
    """
    pairs = blob["compiled_class_hashes_for_migration"]
    if not pairs:
        return []
    compiled_class_hashes = blob["initial_reads"]["compiled_class_hashes"]
    class_hashes_by_casm_v1: Dict[str, List[str]] = {}
    for class_hash, casm_hash in compiled_class_hashes.items():
        class_hashes_by_casm_v1.setdefault(casm_hash, []).append(class_hash)
    migration_pairs: List[List[str]] = []
    for casm_v2, casm_v1 in pairs:
        candidate_class_hashes = class_hashes_by_casm_v1.get(casm_v1, [])
        if len(candidate_class_hashes) != 1:
            raise OsInputBuildError(
                f"cannot recover the class hash for migration pair (v2={casm_v2}, "
                f"v1={casm_v1}): {len(candidate_class_hashes)} entries in "
                "initial_reads.compiled_class_hashes hold that v1 casm hash"
            )
        migration_pairs.append([candidate_class_hashes[0], casm_v2])
    return migration_pairs
```

**File:** crates/blockifier/src/utils.rs (L122-142)
```rust
pub fn should_migrate(
    state_reader: &impl StateReader,
    class_hash: ClassHash,
) -> StateResult<Option<(ClassHash, CompiledClassHashV2ToV1)>> {
    let state_compiled_class_hash = state_reader.get_compiled_class_hash(class_hash)?;
    match state_compiled_class_hash {
        // Class hash does not exist in the state, or is a Cairo 0 class.
        CompiledClassHash(hash) if hash == StarkHash::ZERO => Ok(None),
        state_compiled_class_hash => {
            let compiled_class_hash_v2 = state_reader.get_compiled_class_hash_v2(
                class_hash,
                &state_reader.get_compiled_class(class_hash)?,
            )?;
            // If the state compiled class hash is compiled class hash v2, the class should not
            // migrate.
            if state_compiled_class_hash == compiled_class_hash_v2 {
                return Ok(None);
            }
            Ok(Some((class_hash, (compiled_class_hash_v2, state_compiled_class_hash))))
        }
    }
```

**File:** crates/apollo_batcher_types/src/batcher_types.rs (L155-170)
```rust
#[derive(Debug, Serialize, Deserialize, PartialEq)]
#[cfg_attr(any(test, feature = "testing"), derive(Default))]
pub struct CentralObjects {
    pub execution_infos: IndexMap<TransactionHash, TransactionExecutionInfo>,
    pub bouncer_weights: BouncerWeights,
    pub compressed_state_diff: Option<CommitmentStateDiff>,
    pub casm_hash_computation_data_sierra_gas: CasmHashComputationData,
    pub casm_hash_computation_data_proving_gas: CasmHashComputationData,
    pub compiled_class_hashes_for_migration: CompiledClassHashesForMigration,
    pub parent_proposal_commitment: Option<ProposalCommitment>,
    #[cfg(feature = "os_input")]
    pub accessed_keys: AccessedKeys,
    /// Pre-block read values the OS needs to replay the block.
    #[cfg(feature = "os_input")]
    pub initial_reads: StateMaps,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L87-118)
```text
// Migrates contract classes from v1 (Poseidon-based CASM hash) to v2 (Blake-based CASM hash).
// The class hashes are guessed, and should at least cover the non-migrated classes that
// will be executed by the block.
// Hint arguments:
// block_input - The block input containing the class hashes to migrate.
func migrate_classes_to_v2_casm_hash{
    poseidon_ptr: PoseidonBuiltin*, range_check_ptr, contract_class_changes: DictAccess*
}(n_classes: felt, block_context: BlockContext*) {
    alloc_locals;
    if (n_classes == 0) {
        return ();
    }
    // Guess the class hash and compiled class fact.
    local class_hash;
    local compiled_class_fact: CompiledClassFact*;
    %{ GetClassHashAndCompiledClassFact %}
    let compiled_class = compiled_class_fact.compiled_class;
    // Compute the full compiled class hash, both v1 and v2.
    // This hint enters a new scope that contains the bytecode segment structure of the class.
    %{ EnterScopeWithBytecodeSegmentStructure %}
    let (casm_hash_v1) = poseidon_compiled_class_hash(compiled_class, full_contract=TRUE);
    let (casm_hash_v2) = blake_compiled_class_hash(compiled_class, full_contract=TRUE);
    %{ vm_exit_scope() %}
    // Sanity check: verify the guessed v2 hash.
    assert compiled_class_fact.hash = casm_hash_v2;
    // Update the casm hash from v1 to v2.
    dict_update{dict_ptr=contract_class_changes}(
        key=class_hash, prev_value=casm_hash_v1, new_value=casm_hash_v2
    );
    migrate_classes_to_v2_casm_hash(n_classes=n_classes - 1, block_context=block_context);
    return ();
}
```

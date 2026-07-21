### Title
Stale Compiled Class Hash in `ClassManager::add_class` Permanently Rejects Valid Declare Transactions After Compiler Upgrade — (`crates/apollo_class_manager/src/class_manager.rs`)

### Summary

`ClassManager::add_class` returns a cached `executable_class_hash_v2` without recompiling when a class already exists in storage. If the Sierra compiler is upgraded between the time a class is first compiled and a subsequent declare attempt, the cached hash is stale. The `TransactionConverter` then permanently rejects any new Declare V3 transaction whose `compiled_class_hash` was computed with the new compiler, because it will never match the cached old hash. There is no mechanism to invalidate or update the cached hash.

### Finding Description

**Step 1 — Cache-hit early return in `add_class`:** [1](#0-0) 

When `executable_class_hash_v2` is already stored for a given `class_hash`, `add_class` returns immediately with the cached value, bypassing the compiler entirely.

**Step 2 — `CachedClassStorage::set_class` also silently no-ops on existing entries:** [2](#0-1) 

Even the lower-level `set_class` returns `Ok(())` without writing if the class is already cached. There is no `update_class` or `delete_class` path in the public API, making the cached hash immutable once written.

**Step 3 — `TransactionConverter` enforces exact hash equality:** [3](#0-2) 

The converter calls `add_class`, receives the cached (stale) `executable_class_hash_v2`, and immediately rejects the transaction if `tx.compiled_class_hash != executable_class_hash_v2`. The error is terminal — the transaction is dropped, not queued for retry.

**Step 4 — No recompilation path exists:**

`add_class_and_executable_unsafe` also calls `set_class`, which silently no-ops on cached entries: [4](#0-3) 

### Impact Explanation

After a Sierra compiler upgrade that produces different CASM for the same Sierra source (a routine event during protocol upgrades), any Declare V3 transaction whose `compiled_class_hash` was computed with the new compiler will be permanently rejected at the gateway/RPC admission layer. The class is stuck in the cache with the old hash. This matches:

> **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

Additionally, an adversary who knows a Sierra class's source (e.g., from a public repository) can pre-submit a declare transaction before the legitimate owner, seeding the cache with the old-compiler hash. After a compiler upgrade, the legitimate owner's declare is permanently blocked — a griefing attack with no recovery path.

### Likelihood Explanation

Sierra compiler upgrades that change CASM output for existing classes are a normal operational event (the diff regression files confirm versioned-constants and compiler behavior change across protocol versions). The `FsClassStorage` persists the stale hash across restarts. No operator tooling exists to invalidate a single cached entry.

### Recommendation

1. **Version-stamp cached entries**: Store the compiler version alongside `executable_class_hash_v2`. On `add_class`, if the stored compiler version differs from the current one, recompile and overwrite.
2. **Expose a `recompile_class` operator API**: Allow forced recompilation of a specific `class_hash` to recover from stale cache entries after a compiler upgrade.
3. **Alternatively**: Make `set_class` in `CachedClassStorage` overwrite unconditionally when called from `add_class` (not from the unsafe path), so a fresh compilation always wins.

### Proof of Concept

1. Compiler version V1 is active. User A submits `Declare V3` for Sierra class `S` with `compiled_class_hash = H1` (Poseidon hash of CASM produced by V1).
2. `ClassManager::add_class` compiles `S` with V1, stores `(class_hash_S → H1)` in `FsClassStorage`.
3. The sequencer operator upgrades the compiler to V2. V2 produces different CASM for `S`, yielding hash `H2 ≠ H1`.
4. User B (or User A retrying) submits `Declare V3` for `S` with `compiled_class_hash = H2` (correct for V2).
5. `add_class` hits the early-return at line 74–79, returns `H1`.
6. `TransactionConverter` checks `H2 != H1` → `CompiledClassHashMismatch` error → transaction rejected.
7. No retry is possible: the cache is immutable, and the user cannot submit `H1` because the new compiler produces `H2`. The class is permanently undeclarable. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_class_manager/src/class_manager.rs (L70-113)
```rust
    #[instrument(skip(self, class), ret, err)]
    pub async fn add_class(&mut self, class: RawClass) -> ClassManagerResult<ClassHashes> {
        let sierra_class = SierraContractClass::try_from(&class)?;
        let class_hash = sierra_class.calculate_class_hash();
        if let Ok(Some(executable_class_hash_v2)) =
            self.classes.get_executable_class_hash_v2(class_hash)
        {
            // Class already exists.
            return Ok(ClassHashes { class_hash, executable_class_hash_v2 });
        }

        let compilation_start_time = Instant::now();
        let (raw_executable_class, executable_class_hash_v2) =
            self.compiler.compile(class.clone()).await.map_err(|err| match err {
                SierraCompilerClientError::SierraCompilerError(error) => {
                    ClassManagerError::SierraCompiler { class_hash, error }
                }
                SierraCompilerClientError::ClientError(error) => {
                    ClassManagerError::Client(error.to_string())
                }
            })?;
        debug!(
            %class_hash,
            compiled_class_hash = %executable_class_hash_v2,
            compilation_elapsed_ms = compilation_start_time.elapsed().as_millis(),
            class_size_bytes =
                class.size().map_or("Failed to get class size".to_owned(), |size| size.to_string()),
            casm_size_bytes =
                raw_executable_class.size().map_or("Failed to get casm size".to_owned(), |size| size.to_string()),
            "Finished compiling class."
        );

        self.validate_class_length(&raw_executable_class)?;
        Self::validate_class_version(&sierra_class)?;
        self.classes.set_class(
            class_hash,
            class,
            executable_class_hash_v2,
            raw_executable_class,
        )?;

        let class_hashes = ClassHashes { class_hash, executable_class_hash_v2 };
        Ok(class_hashes)
    }
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L147-155)
```rust
    pub fn add_class_and_executable_unsafe(
        &mut self,
        class_id: ClassId,
        class: RawClass,
        executable_class_hash_v2: ExecutableClassHash,
        executable_class: RawExecutableClass,
    ) -> ClassManagerResult<()> {
        Ok(self.classes.set_class(class_id, class, executable_class_hash_v2, executable_class)?)
    }
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L106-138)
```rust
    #[instrument(skip(self, class, executable_class), level = "debug", ret, err)]
    fn set_class(
        &mut self,
        class_id: ClassId,
        class: RawClass,
        executable_class_hash_v2: ExecutableClassHash,
        executable_class: RawExecutableClass,
    ) -> Result<(), Self::Error> {
        if self.class_cached(class_id) {
            return Ok(());
        }

        self.storage.set_class(
            class_id,
            class.clone(),
            executable_class_hash_v2,
            executable_class.clone(),
        )?;

        increment_n_classes(CairoClassType::Regular);
        record_class_size(ClassObjectType::Sierra, &class);
        record_class_size(ClassObjectType::Casm, &executable_class);

        // Cache the class.
        // Done after successfully writing to storage as an optimization;
        // does not require atomicity.
        self.classes.set(class_id, class);
        self.executable_classes.set(class_id, executable_class);
        // Cache the executable class hash last; acts as an existence marker.
        self.executable_class_hashes_v2.set(class_id, executable_class_hash_v2);

        Ok(())
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L347-392)
```rust
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => {
                let ClassHashes { class_hash, executable_class_hash_v2 } =
                // TODO(Dori): Make this async and spawn a task to compile and add it to the class manager.
                    self.class_manager_client.add_class(tx.contract_class).await?;
                // TODO(Aviv): Ensure that we do not want to
                // allow declare with compiled class hash v1.
                if tx.compiled_class_hash != executable_class_hash_v2 {
                    return Err(TransactionConverterError::ValidateCompiledClassHashError(
                        ValidateCompiledClassHashError::CompiledClassHashMismatch {
                            computed_class_hash: executable_class_hash_v2,
                            supplied_class_hash: tx.compiled_class_hash,
                        },
                    ));
                }
                (
                    InternalRpcTransactionWithoutTxHash::Declare(InternalRpcDeclareTransactionV3 {
                        sender_address: tx.sender_address,
                        compiled_class_hash: tx.compiled_class_hash,
                        signature: tx.signature,
                        nonce: tx.nonce,
                        class_hash,
                        resource_bounds: tx.resource_bounds,
                        tip: tx.tip,
                        paymaster_data: tx.paymaster_data,
                        account_deployment_data: tx.account_deployment_data,
                        nonce_data_availability_mode: tx.nonce_data_availability_mode,
                        fee_data_availability_mode: tx.fee_data_availability_mode,
                    }),
                    None,
                )
            }
            RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(tx)) => {
                let contract_address = tx.calculate_contract_address()?;
                (
                    InternalRpcTransactionWithoutTxHash::DeployAccount(
                        InternalRpcDeployAccountTransaction {
                            tx: RpcDeployAccountTransaction::V3(tx),
                            contract_address,
                        },
                    ),
                    None,
                )
            }
        };
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

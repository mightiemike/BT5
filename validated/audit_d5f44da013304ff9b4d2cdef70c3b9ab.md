### Title
Gateway `TransactionConverter` unconditionally validates `compiled_class_hash` against V2 (Blake) hash, creating a version-boundary mismatch with `VersionedConstants.block_casm_hash_v1_declares` — (File: crates/apollo_transaction_converter/src/transaction_converter.rs)

### Summary

`TransactionConverter::convert_rpc_tx_to_internal` always validates the user-supplied `compiled_class_hash` against `executable_class_hash_v2` (Blake/V2 hash) returned by the compiler, with no reference to the active `VersionedConstants`. The `VersionedConstants` flag `block_casm_hash_v1_declares` is `false` in versions 0.13.6 and 0.14.0, meaning the blockifier execution layer accepts V1 (Poseidon) compiled class hash Declare transactions in those versions. The gateway converter, however, unconditionally rejects them. This is a version/config boundary mismatch: the admission layer enforces a stricter hash-version policy than the execution layer, causing valid Declare transactions to be rejected before sequencing.

### Finding Description

**Compiler always produces V2 hash.** `SierraCompiler::compile` computes the compiled class hash exclusively with `HashVersion::V2` (Blake):

```rust
let executable_class_hash = executable_class.hash(&HashVersion::V2);
``` [1](#0-0) 

**`ClassManager::add_class` stores and returns only the V2 hash.** When a Declare transaction arrives, `add_class` compiles the Sierra class and returns `ClassHashes { class_hash, executable_class_hash_v2 }`. [2](#0-1) 

**`TransactionConverter` unconditionally rejects any mismatch against V2 hash.** In `convert_rpc_tx_to_internal`, after calling `add_class`, the converter checks:

```rust
if tx.compiled_class_hash != executable_class_hash_v2 {
    return Err(TransactionConverterError::ValidateCompiledClassHashError(
        ValidateCompiledClassHashError::CompiledClassHashMismatch { ... }
    ));
}
```

A developer TODO comment at this exact site reads: `// TODO(Aviv): Ensure that we do not want to allow declare with compiled class hash v1.` — confirming the developers themselves are uncertain whether this unconditional rejection is correct. [3](#0-2) 

**`VersionedConstants.block_casm_hash_v1_declares` is `false` in versions 0.13.6 and 0.14.0.** This flag, when `false`, means the blockifier execution layer does *not* block V1 (Poseidon) hash Declare transactions: [4](#0-3) [5](#0-4) 

**The blockifier's own check is version-gated.** In `DeclareTransaction::run_execute`, the V2-hash enforcement only fires when `block_casm_hash_v1_declares` is `true`:

```rust
if context.tx_context.block_context.versioned_constants.block_casm_hash_v1_declares
    && self.version() >= TransactionVersion::THREE
{
    self.check_compile_class_hash_v2_declaration()?
}
``` [6](#0-5) 

**The two-hash scheme.** `HashVersion::V1` uses Poseidon; `HashVersion::V2` uses Blake2Felt252. They produce different `CompiledClassHash` values for the same CASM: [7](#0-6) [8](#0-7) 

**The divergence.** When the sequencer runs with versioned constants 0.13.6 or 0.14.0:
- Blockifier: `block_casm_hash_v1_declares = false` → accepts a V3 Declare whose `compiled_class_hash` is the Poseidon (V1) hash of the CASM.
- `TransactionConverter` (gateway and consensus paths): always compiles → gets Blake (V2) hash → V1 ≠ V2 → `CompiledClassHashMismatch` → transaction rejected before it ever reaches the blockifier.

The `TransactionConverter` is used in both the gateway path (`convert_rpc_tx_to_internal_rpc_tx`) and the consensus path (`convert_consensus_tx_to_internal_consensus_tx`), so the mismatch affects both new transaction admission and cross-node consensus conversion. [9](#0-8) [10](#0-9) 

### Impact Explanation

**High. Mempool/gateway/RPC admission rejects valid transactions before sequencing.**

A Declare V3 transaction carrying a Poseidon (V1) `compiled_class_hash` is valid under versioned constants 0.13.6 and 0.14.0 (`block_casm_hash_v1_declares = false`). The `TransactionConverter` rejects it at the gateway with `COMPILED_CLASS_HASH_MISMATCH` (RPC error code 60) before the blockifier ever evaluates it. The transaction is permanently excluded from the mempool and from any block, even though the execution layer would have accepted it. In the consensus path, a block proposed by a peer running old versioned constants and containing such a Declare would be rejected by the converter on the receiving node, causing a consensus-level divergence.

### Likelihood Explanation

**Low-Medium.** The mismatch is only reachable when the sequencer is configured with versioned constants 0.13.6 or 0.14.0. Users or tooling that computes `compiled_class_hash` using the Poseidon (V1) algorithm (e.g., older SDKs or direct hash computation) and submits to a node running those constants will trigger the rejection. The developer TODO comment at the exact rejection site confirms the behavior is not yet settled, increasing the probability that the current code is unintentionally over-restrictive.

### Recommendation

Make `convert_rpc_tx_to_internal` version-aware. Pass the active `VersionedConstants` into the converter (or read it from a shared config) and skip the V2-hash check when `block_casm_hash_v1_declares` is `false`. Concretely:

```rust
// Only

### Citations

**File:** crates/apollo_compile_to_casm/src/lib.rs (L69-70)
```rust
        let executable_class_hash = executable_class.hash(&HashVersion::V2);
        let executable_class = ContractClass::V1((executable_class, sierra_version));
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L71-113)
```rust
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L184-202)
```rust
    async fn convert_consensus_tx_to_internal_consensus_tx(
        &self,
        tx: ConsensusTransaction,
    ) -> TransactionConverterResult<(InternalConsensusTransaction, Option<VerifyAndStoreProofTask>)>
    {
        match tx {
            ConsensusTransaction::RpcTransaction(tx) => {
                let (internal_tx, proof_data) = self.convert_rpc_tx_to_internal(tx).await?;
                let task = proof_data.map(|(proof_facts, proof)| {
                    self.spawn_verify_and_store_proof(proof_facts, proof)
                });
                Ok((InternalConsensusTransaction::RpcTransaction(internal_tx), task))
            }
            ConsensusTransaction::L1Handler(tx) => {
                let internal_tx = self.convert_consensus_l1_handler_to_internal_l1_handler(tx)?;
                Ok((InternalConsensusTransaction::L1Handler(internal_tx), None))
            }
        }
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L256-265)
```rust
    async fn convert_rpc_tx_to_internal_rpc_tx(
        &self,
        tx: RpcTransaction,
    ) -> TransactionConverterResult<(InternalRpcTransaction, Option<VerificationHandle>)> {
        let (internal_tx, proof_data) = self.convert_rpc_tx_to_internal(tx).await?;
        let verification_handle = proof_data
            .map(|(proof_facts, proof)| self.spawn_proof_verification(proof_facts, proof))
            .transpose()?;
        Ok((internal_tx, verification_handle))
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L347-360)
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
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_13_6.json (L121-124)
```json
    "enable_reverts": true,
    "enable_casm_hash_migration": false,
    "block_casm_hash_v1_declares": false,
    "strip_vm_frames_in_sierra_gas": false,
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_14_0.json (L121-124)
```json
    "enable_reverts": true,
    "enable_casm_hash_migration": false,
    "block_casm_hash_v1_declares": false,
    "strip_vm_frames_in_sierra_gas": false,
```

**File:** crates/blockifier/src/transaction/transactions.rs (L184-189)
```rust
                if context.tx_context.block_context.versioned_constants.block_casm_hash_v1_declares
                    && self.version() >= TransactionVersion::THREE
                {
                    self.check_compile_class_hash_v2_declaration()?
                }
                try_declare(self, state, class_hash, Some(*compiled_class_hash))?
```

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L17-23)
```rust
#[derive(Clone, Copy, PartialEq)]
pub enum HashVersion {
    /// Poseidon hash.
    V1,
    /// Blake2Felt252 hash.
    V2,
}
```

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L144-149)
```rust
    fn hash(&self, hash_version: &HashVersion) -> CompiledClassHash {
        match hash_version {
            HashVersion::V1 => hash_inner::<Poseidon, EH, NL>(self),
            HashVersion::V2 => hash_inner::<Blake2Felt252, EH, NL>(self),
        }
    }
```

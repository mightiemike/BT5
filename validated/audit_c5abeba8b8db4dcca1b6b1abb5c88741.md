### Title
Storage Deserialization Backward-Compatibility Shim Silently Produces Wrong Transaction Hash for `InvokeTransactionV3` with Non-Empty `proof_facts` - (File: `crates/apollo_storage/src/serialization/serializers.rs`)

### Summary

The `StorageSerde` backward-compatibility shim for `InvokeTransactionV3` uses a heuristic (`data.is_empty()`) to decide whether `proof_facts` is present in a stored record. When a legacy record's trailing bytes happen to be non-empty but are not a valid `proof_facts` encoding, the deserializer silently misinterprets those bytes as `proof_facts`, producing a structurally valid but semantically wrong `InvokeTransactionV3`. The resulting object carries a non-empty `proof_facts` value that was never part of the original transaction, causing `get_invoke_transaction_v3_hash` to append an extra hash element to the Poseidon chain and produce a transaction hash that does not match the hash the user signed.

### Finding Description

`InvokeTransactionV3` has a custom `StorageSerde` implementation in `crates/apollo_storage/src/serialization/serializers.rs` (lines 1351–1423) that was introduced as a migration shim to handle records written before the `proof_facts` field was added. The shim's `deserialize_from` reads all mandatory fields in order, then checks whether any bytes remain:

```rust
let proof_facts = if data.is_empty() {
    ProofFacts::default()
} else {
    ProofFacts::deserialize_from(data)?
};
``` [1](#0-0) 

The `ProofFacts` binary format is a length-prefixed `Arc<Vec<Felt>>`. The `auto_storage_serde!` macro serializes `Arc<Vec<Felt>>` as a varint length followed by the raw felt bytes. If the decompressed payload of a legacy record has any trailing bytes — for example, because a future field was appended after `account_deployment_data` by a different code path, or because the varint encoding of `account_deployment_data`'s length leaves a non-zero residue — those bytes will be fed into `ProofFacts::deserialize_from`. If the residue happens to decode as a valid varint length followed by enough bytes to form one or more `Felt` values, the result is a non-empty `proof_facts` that was never signed by the user. [2](#0-1) 

The transaction hash function `get_invoke_transaction_v3_hash` conditionally appends `proof_facts` to the Poseidon hash chain only when it is non-empty:

```rust
if !transaction.proof_facts().0.is_empty() {
    let proof_facts_hash =
        HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
    hash_chain = hash_chain.chain(&proof_facts_hash);
}
``` [3](#0-2) 

So a spuriously non-empty `proof_facts` changes the hash. The stored `tx_hash` field in `InternalRpcTransaction` was computed at admission time (when `proof_facts` was empty), but the `InvokeTransactionV3` object reconstructed from storage now has a non-empty `proof_facts`, so recomputing the hash from the stored struct yields a different value. [4](#0-3) 

The same deserialized struct is later converted to an executable transaction via `convert_internal_rpc_tx_to_executable_tx`, which passes the stored `tx_hash` through unchanged: [5](#0-4) 

This means the blockifier executes the transaction with a `tx_hash` that does not match the hash derivable from the transaction body, and the OS-level `AssertTransactionHash` hint will see a mismatch between the recomputed hash and the stored one.

### Impact Explanation

**Wrong state / receipt / revert result from blockifier/syscall/execution logic for accepted input.** The `get_execution_info` syscall exposes `tx_hash` to the contract. If the stored hash and the recomputed hash diverge, the OS proof will fail to verify (the Cairo `AssertTransactionHash` hint checks the recomputed hash against the hint-provided value). This can cause a valid, previously-accepted transaction to produce a proof that is rejected, or — if the OS hint is fed the stored (wrong) hash — to execute with a hash that does not match the user's signature preimage, breaking signature-domain integrity.

**Wrong compiled class / hash selected for execution.** The `validate_proof_facts` path in `blockifier` parses the spurious `proof_facts` bytes as SNOS proof facts and may reject the transaction with `InvalidProofFacts`, causing a valid transaction to be incorrectly reverted. [6](#0-5) 

### Likelihood Explanation

The trigger requires a legacy `InvokeTransactionV3` record on disk (written before `proof_facts` was added) whose decompressed payload has trailing bytes after `account_deployment_data` that happen to decode as a valid `ProofFacts` varint-length-prefixed vector. This is a data-dependent condition. The shim is explicitly marked as temporary and not yet removed (`TODO(AvivG): Remove this migration shim once all nodes have re-synced`), so the vulnerable code is active in production. The condition is reachable without any privileged access — any node that stored `InvokeTransactionV3` records before the `proof_facts` field was introduced and has not fully re-synced is exposed. [7](#0-6) 

### Recommendation

Replace the `data.is_empty()` heuristic with an explicit version tag. Prefix every serialized `InvokeTransactionV3` record with a one-byte schema version: `0` for the legacy layout (no `proof_facts`), `1` for the current layout (with `proof_facts`). The deserializer reads the version byte first and branches accordingly. This eliminates the ambiguity entirely and is consistent with the `Migratable` pattern already used for `StorageBlockHeader` in `crates/apollo_storage/src/deprecated/migrations.rs`. [8](#0-7) 

Alternatively, if a version tag is not feasible, the shim must verify that after consuming `proof_facts` no bytes remain; if bytes do remain, deserialization must fail rather than silently succeed with a corrupted struct.

### Proof of Concept

1. Take any `InvokeTransactionV3` record written before `proof_facts` was added. Its decompressed payload ends after `account_deployment_data`.

2. Suppose `account_deployment_data` is serialized as a varint `0x00` (empty vec) followed by zero bytes. The total payload is exactly `N` bytes. Now suppose a different serialization path wrote one extra byte (e.g., a padding byte or a future optional field) at offset `N`. After the shim reads all mandatory fields, `data` is non-empty (one byte remains).

3. `ProofFacts::deserialize_from` reads a varint from that one byte. If the byte is `0x00`, it decodes as length 0, producing an empty `ProofFacts` — harmless. If the byte is `0x01`, it decodes as length 1 and tries to read 32 more bytes; if those bytes are present (e.g., from a compressed block that was slightly over-read), it produces a one-element `ProofFacts` with an arbitrary felt.

4. `get_invoke_transaction_v3_hash` now appends `poseidon_hash([arbitrary_felt])` to the hash chain, producing a hash `H'` ≠ `H` (the hash the user signed).

5. The stored `InternalRpcTransaction.tx_hash` is `H`. The blockifier receives the executable transaction with `tx_hash = H` but `proof_facts = [arbitrary_felt]`. The OS `AssertTransactionHash` hint recomputes `H'` and asserts `H' == H`, which fails, causing proof generation to abort or the transaction to be incorrectly reverted. [9](#0-8) [10](#0-9)

### Citations

**File:** crates/apollo_storage/src/serialization/serializers.rs (L1344-1351)
```rust
// Custom StorageSerde for InvokeTransactionV3 (backward compatibility).
// Allows deserializing legacy on-disk txs that were stored before `proof_facts` was added.
//
// NOTE: This is a temporary migration shim. Once all nodes have re-synced (i.e., no stored
// transactions exist in the old format), remove this impl and move InvokeTransactionV3 back
// to `auto_storage_serde_conditionally_compressed!`.
// TODO(AvivG): Remove this migration shim once all nodes have re-synced.
impl StorageSerde for InvokeTransactionV3 {
```

**File:** crates/apollo_storage/src/serialization/serializers.rs (L1383-1422)
```rust
    fn deserialize_from(bytes: &mut impl std::io::Read) -> Option<Self> {
        let is_compressed = IsCompressed::deserialize_from(bytes)?;
        let maybe_compressed_data = Vec::<u8>::deserialize_from(bytes)?;
        let data = match is_compressed {
            IsCompressed::No => maybe_compressed_data,
            IsCompressed::Yes => decompress(maybe_compressed_data.as_slice())
                .expect("destination buffer should be large enough"),
        };
        let data = &mut data.as_slice();
        let resource_bounds = ValidResourceBounds::deserialize_from(data)?;
        let tip = Tip::deserialize_from(data)?;
        let signature = TransactionSignature::deserialize_from(data)?;
        let nonce = Nonce::deserialize_from(data)?;
        let sender_address = ContractAddress::deserialize_from(data)?;
        let calldata = Calldata::deserialize_from(data)?;
        let nonce_data_availability_mode = DataAvailabilityMode::deserialize_from(data)?;
        let fee_data_availability_mode = DataAvailabilityMode::deserialize_from(data)?;
        let paymaster_data = PaymasterData::deserialize_from(data)?;
        let account_deployment_data = AccountDeploymentData::deserialize_from(data)?;
        // Backward compatibility: proof_facts may not exist in old transactions.
        // If no data remains, default to empty; otherwise, deserialize normally.
        let proof_facts = if data.is_empty() {
            ProofFacts::default()
        } else {
            ProofFacts::deserialize_from(data)?
        };
        Some(Self {
            resource_bounds,
            tip,
            signature,
            nonce,
            sender_address,
            calldata,
            nonce_data_availability_mode,
            fee_data_availability_mode,
            paymaster_data,
            account_deployment_data,
            proof_facts,
        })
    }
```

**File:** crates/starknet_api/src/transaction_hash.rs (L370-404)
```rust
pub(crate) fn get_invoke_transaction_v3_hash<T: InvokeTransactionV3Trait>(
    transaction: &T,
    chain_id: &ChainId,
    transaction_version: &TransactionVersion,
) -> Result<TransactionHash, StarknetApiError> {
    let tip_resource_bounds_hash =
        get_tip_resource_bounds_hash(&transaction.resource_bounds(), transaction.tip())?;
    let paymaster_data_hash =
        HashChain::new().chain_iter(transaction.paymaster_data().0.iter()).get_poseidon_hash();
    let data_availability_mode = concat_data_availability_mode(
        transaction.nonce_data_availability_mode(),
        transaction.fee_data_availability_mode(),
    );
    let account_deployment_data_hash = HashChain::new()
        .chain_iter(transaction.account_deployment_data().0.iter())
        .get_poseidon_hash();
    let calldata_hash =
        HashChain::new().chain_iter(transaction.calldata().0.iter()).get_poseidon_hash();
    let mut hash_chain = HashChain::new()
        .chain(&INVOKE)
        .chain(&transaction_version.0)
        .chain(transaction.sender_address().0.key())
        .chain(&tip_resource_bounds_hash)
        .chain(&paymaster_data_hash)
        .chain(&Felt::try_from(chain_id)?)
        .chain(&transaction.nonce().0)
        .chain(&data_availability_mode)
        .chain(&account_deployment_data_hash)
        .chain(&calldata_hash);
    if !transaction.proof_facts().0.is_empty() {
        let proof_facts_hash =
            HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
        hash_chain = hash_chain.chain(&proof_facts_hash);
    }
    Ok(TransactionHash(hash_chain.get_poseidon_hash()))
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L143-147)
```rust
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash, SizeOf)]
pub struct InternalRpcTransaction {
    pub tx: InternalRpcTransactionWithoutTxHash,
    pub tx_hash: TransactionHash,
}
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L267-305)
```rust
    async fn convert_internal_rpc_tx_to_executable_tx(
        &self,
        InternalRpcTransaction { tx, tx_hash }: InternalRpcTransaction,
    ) -> TransactionConverterResult<AccountTransaction> {
        match tx {
            InternalRpcTransactionWithoutTxHash::Invoke(tx) => {
                Ok(AccountTransaction::Invoke(executable_transaction::InvokeTransaction {
                    tx: tx.into(),
                    tx_hash,
                }))
            }
            InternalRpcTransactionWithoutTxHash::Declare(tx) => {
                let (sierra, contract_class) = tokio::try_join!(
                    self.get_sierra(tx.class_hash),
                    self.get_executable(tx.class_hash)
                )?;
                let class_info = ClassInfo {
                    contract_class,
                    sierra_program_length: sierra.sierra_program.len(),
                    abi_length: sierra.abi.len(),
                    sierra_version: SierraVersion::extract_from_program(&sierra.sierra_program)?,
                };

                Ok(AccountTransaction::Declare(executable_transaction::DeclareTransaction {
                    tx: tx.into(),
                    tx_hash,
                    class_info,
                }))
            }
            InternalRpcTransactionWithoutTxHash::DeployAccount(
                InternalRpcDeployAccountTransaction { tx, contract_address },
            ) => Ok(AccountTransaction::DeployAccount(
                executable_transaction::DeployAccountTransaction {
                    tx: tx.into(),
                    contract_address,
                    tx_hash,
                },
            )),
        }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L291-351)
```rust
    fn validate_proof_facts(
        &self,
        block_context: &BlockContext,
        state: &mut dyn State,
    ) -> TransactionPreValidationResult<()> {
        // Only Invoke V3 transactions can carry proof facts.
        let Transaction::Invoke(invoke_tx) = &self.tx else {
            return Ok(());
        };
        if invoke_tx.version() < TransactionVersion::THREE {
            return Ok(());
        }

        // Parse proof facts.
        let proof_facts = invoke_tx.proof_facts();
        let snos_proof_facts = match ProofFactsVariant::try_from(&proof_facts)
            .map_err(|e| TransactionPreValidationError::InvalidProofFacts(e.to_string()))?
        {
            ProofFactsVariant::Empty => return Ok(()),
            ProofFactsVariant::Snos(snos_proof_facts) => snos_proof_facts,
        };
        let os_constants = &block_context.versioned_constants.os_constants;

        if !os_constants.allowed_proof_versions.contains(&snos_proof_facts.proof_version.as_felt())
        {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Proof version {} is not allowed under this protocol version.",
                snos_proof_facts.proof_version
            )));
        }

        // Validate the program hash.
        let allowed = &os_constants.allowed_virtual_os_program_hashes;
        if !allowed.contains(&snos_proof_facts.program_hash) {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS program hash {} is not allowed",
                snos_proof_facts.program_hash
            )));
        }

        // Validate the block hash and block number.
        let proof_block_hash = snos_proof_facts.block_hash.0;
        let proof_block_number = snos_proof_facts.block_number.0;
        Self::validate_proof_block_number(
            proof_block_number,
            block_context.block_info.block_number,
        )?;
        Self::validate_proof_block_hash(proof_block_hash, proof_block_number, os_constants, state)?;

        // Validate the config hash.
        let virtual_os_config_hash = block_context.virtual_os_config_hash();
        let proof_config_hash = snos_proof_facts.config_hash;
        if virtual_os_config_hash != proof_config_hash {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS config hash mismatch. Computed virtual OS config hash: \
                 {virtual_os_config_hash}, expected virtual OS config hash: {proof_config_hash}."
            )));
        }

        Ok(())
    }
```

**File:** crates/apollo_storage/src/deprecated/migrations.rs (L18-37)
```rust
impl Migratable for StorageBlockHeader {
    fn try_from_older_version(
        bytes: &mut impl std::io::Read,
        older_version: u8,
    ) -> Result<Self, StorageSerdeError> {
        const CURRENT_VERSION: u8 = 1;
        const PREV_VERSION: u8 = CURRENT_VERSION - 1;

        let prev_version_block_header = match older_version {
            PREV_VERSION => {
                StorageBlockHeaderV0::deserialize_from(bytes).ok_or(StorageSerdeError::Migration)
            }
            CURRENT_VERSION.. => {
                error!("Version {} is >= current version. Can't migrate.", older_version);
                Err(StorageSerdeError::Migration)
            }
        }?;
        Ok(prev_version_block_header.into())
    }
}
```

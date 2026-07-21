### Title
Gateway-only `account_deployment_data`/`paymaster_data` emptiness invariant bypassed via P2P consensus path, causing blockifier–OS execution divergence — (`crates/apollo_gateway/src/stateless_transaction_validator.rs`, `crates/apollo_transaction_converter/src/transaction_converter.rs`, `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

The gateway's `StatelessTransactionValidator` explicitly enforces that `account_deployment_data` and `paymaster_data` are empty for Invoke V3 and Declare V3 transactions, with the comment "The Starknet OS enforces that the deployer data is empty." However, this check is applied **only** on the RPC/gateway ingestion path. Transactions that arrive through the P2P consensus proposal path (`ConsensusTransaction` → `convert_consensus_tx_to_internal_consensus_tx`) bypass the stateless validator entirely. The blockifier also does not validate these fields; it passes them through to the execution context unchanged. The Starknet OS Cairo program, however, contains a hard `assert account_deployment_data_size = 0` inside `compute_declare_transaction_hash`. A malicious consensus proposer can therefore include a Declare V3 transaction with a non-empty `account_deployment_data` in a proposal; the blockifier will execute and commit the block, but the OS will abort with an assertion failure when attempting to prove it, producing an unprovable committed block.

---

### Finding Description

**Invariant enforced at the gateway only:**

`StatelessTransactionValidator::validate_empty_account_deployment_data` and `validate_empty_paymaster_data` are called unconditionally inside `StatelessTransactionValidator::validate`, which is invoked from `Gateway::add_tx_inner`: [1](#0-0) [2](#0-1) 

The comment is explicit: the OS enforces emptiness; the gateway check is a pre-filter to prevent OS failures.

**Consensus path skips the stateless validator entirely:**

When a validator receives a proposal from a peer, `handle_proposal_part` calls `transaction_converter.convert_consensus_tx_to_internal_consensus_tx(tx)` for every transaction in the batch: [3](#0-2) 

`convert_consensus_tx_to_internal_consensus_tx` delegates to `convert_rpc_tx_to_internal`: [4](#0-3) 

`convert_rpc_tx_to_internal` performs class-hash validation for Declare and contract-address derivation for DeployAccount, but **never calls `StatelessTransactionValidator`**: [5](#0-4) 

**Blockifier passes `account_deployment_data` through without validation:**

`DeclareTransaction::create_tx_info` copies the field verbatim into `CurrentTransactionInfo`: [6](#0-5) 

The hint processor then allocates a VM segment for it and exposes it to the account contract via `get_execution_info`: [7](#0-6) 

No assertion or rejection occurs in the blockifier for a non-empty value.

**OS hard-asserts the field is zero:**

Inside `compute_declare_transaction_hash` in the OS Cairo program: [8](#0-7) 

Line 274: `assert account_deployment_data_size = 0;` — this is an unconditional Cairo `assert`, not a revert. If it fails, the entire OS run aborts; the block cannot be proved.

**Protobuf deserialization carries the field without restriction:**

`TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction` reconstructs the full `RpcDeclareTransactionV3` including `account_deployment_data` from the wire: [9](#0-8) 

---

### Impact Explanation

A malicious consensus proposer crafts a `DeclareV3` transaction with `account_deployment_data = [0x1]`. The receiving validator's `validate_proposal` loop converts it without stateless checks, forwards it to the batcher, and the blockifier executes it successfully (the account's `__validate__` sees the non-empty field but does not necessarily reject it). The block is committed. When the prover runs the OS over the committed block, `compute_declare_transaction_hash` hits `assert account_deployment_data_size = 0` and aborts. The committed block is permanently unprovable. This matches **Critical — Wrong state/revert result from blockifier/execution logic for accepted input**: the blockifier produces and commits a state transition that the OS cannot certify, breaking the execution–proving consistency invariant.

---

### Likelihood Explanation

Triggering this requires controlling a consensus validator seat (proposer role for at least one round). In a permissioned or small-validator-set deployment this is a realistic threat from a compromised or malicious validator. The bypass itself requires zero cryptographic work beyond constructing a valid-looking Declare transaction with one extra felt in `account_deployment_data`.

---

### Recommendation

**Short term:** Mirror the gateway's `validate_empty_account_deployment_data` and `validate_empty_paymaster_data` checks inside `convert_rpc_tx_to_internal` (or in a shared pre-conversion validation function called from both the gateway and the consensus converter). This closes the gap between the two ingestion paths.

**Long term:** Introduce a single canonical `validate_tx_fields_for_os_compatibility` function that is called at every boundary where an `RpcTransaction` is converted to an internal representation — gateway, consensus, and any future P2P sync path — so that OS-enforced invariants cannot be bypassed by choosing an alternative ingestion route.

---

### Proof of Concept

1. Attacker controls validator `V` and is selected as proposer for round `R`.
2. `V` constructs `RpcDeclareTransactionV3 { account_deployment_data: AccountDeploymentData(vec![Felt::ONE]), ... }` with otherwise valid fields and a valid signature over the resulting hash.
3. `V` wraps it in `ConsensusTransaction::RpcTransaction(RpcTransaction::Declare(...))`, serialises it via `From<ConsensusTransaction> for protobuf::ConsensusTransaction`, and streams it as a `ProposalPart::Transactions` batch to peer validators.
4. Each peer's `handle_proposal_part` calls `convert_consensus_tx_to_internal_consensus_tx` — **no** `StatelessTransactionValidator::validate` is invoked; `account_deployment_data` is preserved.
5. The batcher receives the `InternalConsensusTransaction` and the blockifier executes the Declare. `DeclareTransaction::create_tx_info` stores `account_deployment_data: vec![Felt::ONE]` in `CurrentTransactionInfo`. No blockifier assertion fires; the transaction is accepted and the block is committed.
6. The prover runs the OS over the committed block. `compute_declare_transaction_hash` is called with `account_deployment_data_size = 1`. The Cairo `assert account_deployment_data_size = 0` (line 274 of `transaction_hash.cairo`) fails. The OS run aborts; the block is unprovable.

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L37-39)
```rust
        Self::validate_contract_address(tx)?;
        Self::validate_empty_account_deployment_data(tx)?;
        Self::validate_empty_paymaster_data(tx)?;
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L100-118)
```rust
    /// The Starknet OS enforces that the deployer data is empty. We add this validation here in the
    /// gateway to prevent transactions from failing the OS.
    fn validate_empty_account_deployment_data(
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let account_deployment_data = match tx {
            RpcTransaction::DeployAccount(_) => return Ok(()),
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => &tx.account_deployment_data,
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx)) => &tx.account_deployment_data,
        };

        if account_deployment_data.is_empty() {
            Ok(())
        } else {
            Err(StatelessTransactionValidatorError::NonEmptyField {
                field_name: "account_deployment_data".to_string(),
            })
        }
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L598-617)
```rust
        Some(ProposalPart::Transactions(TransactionBatch { transactions: txs })) => {
            // TODO(guyn): check that the length of txs and the number of batches we receive is not
            // so big it would fill up the memory (in case of a malicious proposal)
            debug!("Received transaction batch with {} txs", txs.len());
            let conversion_results =
                futures::future::join_all(txs.into_iter().map(|tx| {
                    transaction_converter.convert_consensus_tx_to_internal_consensus_tx(tx)
                }))
                .await
                .into_iter()
                .collect::<Result<Vec<_>, _>>();
            let conversion_results = match conversion_results {
                Ok(results) => results,
                Err(e) => {
                    return HandledProposalPart::Failed(format!(
                        "Failed to convert transactions. Stopping the build of the current \
                         proposal. {e:?}"
                    ));
                }
            };
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L334-393)
```rust
    async fn convert_rpc_tx_to_internal(
        &self,
        tx: RpcTransaction,
    ) -> TransactionConverterResult<(InternalRpcTransaction, Option<(ProofFacts, Proof)>)> {
        let (tx_without_hash, proof_data) = match tx {
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx)) => {
                let proof_data = if tx.proof_facts.is_empty() {
                    None
                } else {
                    Some((tx.proof_facts.clone(), tx.proof.clone()))
                };
                (InternalRpcTransactionWithoutTxHash::Invoke(tx.into()), proof_data)
            }
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
    }
```

**File:** crates/blockifier/src/transaction/transactions.rs (L222-233)
```rust
            starknet_api::transaction::DeclareTransaction::V3(tx) => {
                TransactionInfo::Current(CurrentTransactionInfo {
                    common_fields,
                    resource_bounds: tx.resource_bounds,
                    tip: tx.tip,
                    nonce_data_availability_mode: tx.nonce_data_availability_mode,
                    fee_data_availability_mode: tx.fee_data_availability_mode,
                    paymaster_data: tx.paymaster_data.clone(),
                    account_deployment_data: tx.account_deployment_data.clone(),
                    proof_facts: ProofFacts::default(),
                })
            }
```

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L439-454)
```rust
                let (tx_account_deployment_data_start_ptr, tx_account_deployment_data_end_ptr) =
                    &self.allocate_data_segment(vm, &context.account_deployment_data.0)?;

                let (tx_proof_facts_start_ptr, tx_proof_facts_end_ptr) =
                    &self.allocate_data_segment(vm, &context.proof_facts.0)?;

                tx_data.extend_from_slice(&[
                    tx_resource_bounds_start_ptr.into(),
                    tx_resource_bounds_end_ptr.into(),
                    Felt::from(context.tip.0).into(),
                    tx_paymaster_data_start_ptr.into(),
                    tx_paymaster_data_end_ptr.into(),
                    Felt::from(context.nonce_data_availability_mode).into(),
                    Felt::from(context.fee_data_availability_mode).into(),
                    tx_account_deployment_data_start_ptr.into(),
                    tx_account_deployment_data_end_ptr.into(),
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L263-292)
```text
// See comment above `compute_invoke_transaction_hash()`.
func compute_declare_transaction_hash{range_check_ptr, poseidon_ptr: PoseidonBuiltin*}(
    common_fields: CommonTxFields*,
    class_hash: felt,
    compiled_class_hash: felt,
    account_deployment_data_size: felt,
    account_deployment_data: felt*,
) -> felt {
    alloc_locals;

    // TODO(Noa, 01/01/2026): remove the following `assert` once the field is supported.
    assert account_deployment_data_size = 0;
    with_attr error_message("Invalid transaction version: {version}.") {
        assert common_fields.version = 3;
    }

    let hash_state: PoseidonHashState = poseidon_hash_init();
    with hash_state {
        hash_tx_common_fields(common_fields=common_fields);
        poseidon_hash_update_with_nested_hash(
            data_ptr=account_deployment_data, data_length=account_deployment_data_size
        );
        // Add the class hash to the hash state.
        poseidon_hash_update_single(item=class_hash);
        poseidon_hash_update_single(item=compiled_class_hash);
    }
    let transaction_hash = poseidon_hash_finalize(hash_state=hash_state);

    return transaction_hash;
}
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L1027-1053)
```rust
impl TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ConsensusTransaction) -> Result<Self, Self::Error> {
        let txn = value.txn.ok_or(missing("ConsensusTransaction::txn"))?;
        let txn = match txn {
            protobuf::consensus_transaction::Txn::DeclareV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::Declare(
                    RpcDeclareTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::DeployAccountV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::DeployAccount(
                    RpcDeployAccountTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::InvokeV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::Invoke(
                    RpcInvokeTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::L1Handler(txn) => {
                ConsensusTransaction::L1Handler(txn.try_into()?)
            }
        };
        Ok(txn)
    }
}
```

### Title
`allow_client_side_proving` Flag Not Enforced on Consensus Proposal Validation Path — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

The `allow_client_side_proving` flag in `StatelessTransactionValidatorConfig` is the sequencer's analog of the Notional vault `ENABLED` flag. It is checked on the gateway/RPC admission path but is completely absent from the consensus proposal validation path. A malicious proposer can include `proof_facts`-bearing invoke transactions in a block proposal; a validator node that has explicitly set `allow_client_side_proving = false` will still accept, cryptographically verify, and commit those transactions.

### Finding Description

**The flag and where it is enforced**

`StatelessTransactionValidatorConfig::allow_client_side_proving` is the single switch that controls whether the node accepts client-side proving transactions. When `false`, `StatelessTransactionValidator::validate_client_side_proving_allowed` rejects any `RpcInvokeTransaction::V3` whose `proof_facts` or `proof` fields are non-empty:

```rust
fn validate_client_side_proving_allowed(
    &self,
    tx: &RpcInvokeTransaction,
) -> StatelessTransactionValidatorResult<()> {
    if self.config.allow_client_side_proving {
        return Ok(());
    }
    let RpcInvokeTransaction::V3(tx) = tx;
    let has_proof_data = !tx.proof_facts.is_empty() || !tx.proof.is_empty();
    if has_proof_data {
        return Err(StatelessTransactionValidatorError::ClientSideProvingNotAllowed);
    }
    Ok(())
}
``` [1](#0-0) 

This check is called from `StatelessTransactionValidator::validate`, which is invoked only on the HTTP/RPC gateway path: [2](#0-1) 

**The unguarded consensus path**

During proposal validation, incoming `ConsensusTransaction` objects are converted directly via `transaction_converter.convert_consensus_tx_to_internal_consensus_tx(tx)` with no `allow_client_side_proving` check anywhere in the call chain: [3](#0-2) 

`convert_consensus_tx_to_internal_consensus_tx` calls the shared `convert_rpc_tx_to_internal` helper, which extracts `proof_data` and spawns a `VerifyAndStoreProofTask` unconditionally whenever `proof_facts` is non-empty: [4](#0-3) [5](#0-4) 

The proof verification task calls `starknet_proof_verifier::verify_proof`, which runs the full circuit verifier: [6](#0-5) 

A grep across the entire repository confirms `allow_client_side_proving` appears only in gateway-scoped files (`stateless_transaction_validator.rs`, `config.rs`, `config_schema.json`, deployment JSON configs, and tests) — never in any consensus or batcher code path.

**The production default is `true`** [7](#0-6) [8](#0-7) 

The flag defaults to `true`, so the gap is latent. It becomes exploitable the moment an operator sets `allow_client_side_proving = false` — the exact scenario in which the gate is supposed to matter.

### Impact Explanation

When an operator sets `allow_client_side_proving = false` (e.g., to disable the feature while a vulnerability in the proof-verification circuit is being patched), the intent is that no node-local code path should process client-side proving transactions. The gateway enforces this for user-submitted transactions. However, a malicious or misconfigured proposer can include `proof_facts`-bearing invoke transactions in a block proposal. The validator node will:

1. Accept the transactions through `validate_proposal` without any flag check.
2. Spawn `VerifyAndStoreProofTask` instances that run the full `privacy_circuit_verify_v1` verifier.
3. On success, store the proof in the proof manager and commit the transaction to the block.

This means the `allow_client_side_proving = false` configuration provides no protection against the consensus path — exactly the same asymmetry as the original Notional bug where `ENABLED = false` blocked `enterVault`/`rollVaultPosition` but not `deleverageAccount`.

The impact maps to **High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing**: the node's own admission policy is bypassed for an entire class of transactions, and the proof-verification code (the component the operator is trying to isolate) is still executed.

### Likelihood Explanation

Likelihood is **Medium**. The flag defaults to `true`, so the gap is dormant in normal operation. It becomes relevant only when an operator explicitly disables client-side proving, which is a deliberate emergency or staged-rollout action. In that scenario, any proposer in the consensus network (including a Byzantine one) can trivially trigger the unguarded path by crafting a valid-looking invoke V3 transaction with non-empty `proof_facts`.

### Recommendation

Add an `allow_client_side_proving` check inside `handle_proposal_part` in `validate_proposal.rs`, mirroring the gateway check. The simplest approach is to thread the flag (or a dedicated `ConsensusConfig` field) into `ProposalValidateArguments` and reject any `ConsensusTransaction::RpcTransaction` whose `proof_facts` is non-empty when the flag is `false`:

```rust
// In handle_proposal_part, before convert_consensus_tx_to_internal_consensus_tx:
if !allow_client_side_proving {
    for tx in &txs {
        if let ConsensusTransaction::RpcTransaction(
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(ref invoke))
        ) = tx {
            if !invoke.proof_facts.is_empty() {
                return HandledProposalPart::Invalid(
                    "client-side proving is disabled".to_string()
                );
            }
        }
    }
}
```

Alternatively, expose the flag through `TransactionConverter` so the converter itself can enforce the policy uniformly across both the gateway and consensus paths.

### Proof of Concept

1. Deploy two sequencer nodes: validator with `allow_client_side_proving = false`, proposer with `allow_client_side_proving = true`.
2. Submit an invoke V3 transaction with valid `proof_facts` and `proof` to the proposer's gateway. It is accepted.
3. The proposer includes the transaction in a block proposal and broadcasts it via consensus.
4. The validator's `validate_proposal` receives the `ProposalPart::Transactions` batch containing the proof-bearing transaction.
5. `convert_consensus_tx_to_internal_consensus_tx` is called with no flag check; `spawn_verify_and_store_proof` is spawned.
6. The proof verifier runs; on success the transaction is forwarded to the batcher and committed.
7. The validator has processed a client-side proving transaction despite `allow_client_side_proving = false`.

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L31-54)
```rust
impl StatelessTransactionValidator {
    #[instrument(skip(self), level = Level::INFO)]
    pub fn validate(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        // TODO(Arni, 1/5/2024): Add a mechanism that validate the sender address is not blocked.
        // TODO(Arni, 1/5/2024): Validate transaction version.

        Self::validate_contract_address(tx)?;
        Self::validate_empty_account_deployment_data(tx)?;
        Self::validate_empty_paymaster_data(tx)?;
        self.validate_resource_bounds(tx)?;
        self.validate_tx_size(tx)?;
        self.validate_nonce_data_availability_mode(tx)?;
        self.validate_fee_data_availability_mode(tx)?;

        if let RpcTransaction::Invoke(invoke_tx) = tx {
            self.validate_client_side_proving_allowed(invoke_tx)?;
            self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
        }

        if let RpcTransaction::Declare(declare_tx) = tx {
            self.validate_declare_tx(declare_tx)?;
        }
        Ok(())
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L231-247)
```rust
    fn validate_client_side_proving_allowed(
        &self,
        tx: &RpcInvokeTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if self.config.allow_client_side_proving {
            return Ok(());
        }

        // Reject V3 transactions with proofs when client-side proving is disabled.
        let RpcInvokeTransaction::V3(tx) = tx;
        let has_proof_data = !tx.proof_facts.is_empty() || !tx.proof.is_empty();
        if has_proof_data {
            return Err(StatelessTransactionValidatorError::ClientSideProvingNotAllowed);
        }

        Ok(())
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L598-647)
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

            // Separate internal transactions from verification and store proof tasks. Each task
            // verifies the proof and stores it in the proof manager. Tasks are collected
            // and awaited later in the fin case.
            let (txs, tasks): (
                Vec<InternalConsensusTransaction>,
                Vec<Option<VerifyAndStoreProofTask>>,
            ) = conversion_results.into_iter().unzip();
            verify_and_store_proof_tasks.extend(tasks.into_iter().flatten());

            debug!(
                "Converted transactions to internal representation. hashes={:?}",
                txs.iter().map(|tx| tx.tx_hash()).collect::<Vec<TransactionHash>>()
            );

            content.push(txs.clone());
            let input = SendTxsForProposalInput { proposal_id, txs };
            let response = match batcher.send_txs_for_proposal(input).await {
                Ok(response) => response,
                Err(e) => {
                    return HandledProposalPart::Failed(format!(
                        "Failed to send transactions to batcher: {e:?}"
                    ));
                }
            };
            match response {
                SendTxsForProposalStatus::Processing => HandledProposalPart::Continue,
                SendTxsForProposalStatus::InvalidProposal(err) => HandledProposalPart::Invalid(err),
            }
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L338-393)
```rust
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L395-424)
```rust
    /// Runs proof verification: checks if the proof already exists, and if not, verifies it.
    /// Returns `true` if verification was performed, `false` if skipped (proof already stored).
    /// This is the shared verification logic used by both gateway and consensus flows.
    async fn run_proof_verification(
        proof_facts: ProofFacts,
        proof: Proof,
        proof_manager_client: SharedProofManagerClient,
    ) -> Result<bool, TransactionConverterError> {
        let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;

        if contains_proof {
            return Ok(false);
        }

        let proof_facts_hash = proof_facts.hash();
        let verify_start = Instant::now();
        tokio::task::spawn_blocking(move || {
            starknet_proof_verifier::verify_proof(proof_facts, proof)
        })
        .await
        .expect("proof verification task panicked")?;
        let verify_duration = verify_start.elapsed();
        PROOF_VERIFICATION_LATENCY.record(verify_duration.as_secs_f64());
        info!(
            "Proof verification took: {verify_duration:?} for proof facts hash: \
             {proof_facts_hash:?}"
        );

        Ok(true)
    }
```

**File:** crates/apollo_gateway_config/src/config.rs (L188-204)
```rust
impl Default for StatelessTransactionValidatorConfig {
    fn default() -> Self {
        StatelessTransactionValidatorConfig {
            validate_resource_bounds: true,
            min_gas_price: 8_000_000_000,
            max_l2_gas_amount: 1_210_000_000,
            max_calldata_length: 5000,
            max_signature_length: 4000,
            max_contract_bytecode_size: 81920,
            max_contract_class_object_size: 4089446,
            min_sierra_version: VersionId::new(1, 1, 0),
            max_sierra_version: VersionId::new(1, 9, usize::MAX),
            allow_client_side_proving: true,
            max_proof_size: 480000,
        }
    }
}
```

**File:** crates/apollo_node/resources/config_schema.json (L3152-3156)
```json
  "gateway_config.static_config.stateless_tx_validator_config.allow_client_side_proving": {
    "description": "If true, allows transactions with non-empty proof_facts or proof fields.",
    "privacy": "Public",
    "value": true
  },
```

### Title
Consensus Proposal Path Bypasses `check_declare_permissions()` Allowing Unauthorized Declare Transactions to Execute — (File: `crates/apollo_gateway/src/gateway.rs`)

### Summary

`GenericGateway::add_tx_inner()` enforces `block_declare` and `authorized_declarer_accounts` restrictions via `check_declare_permissions()` before admitting declare transactions. The consensus validation path (`validate_proposal()` → `convert_consensus_tx_to_internal_consensus_tx()` → `send_txs_for_proposal()`) never calls this function, so a malicious proposer can include declare transactions from unauthorized senders in a consensus proposal and have them executed on every validator node, bypassing the operator-configured admission controls entirely.

### Finding Description

**Enforced path — `add_tx_inner()` in `crates/apollo_gateway/src/gateway.rs`:**

```rust
if let RpcTransaction::Declare(ref declare_tx) = tx {
    if let Err(e) = self.check_declare_permissions(declare_tx) {   // ← enforced
        metric_counters.record_add_tx_failure(&e);
        return Err(e);
    }
}
// Perform stateless validations.
self.stateless_tx_validator.validate(&tx)?;
```

`check_declare_permissions()` enforces two controls:

1. `block_declare` — rejects all declare transactions when `true`
2. `authorized_declarer_accounts` — rejects declares whose `sender_address` is not in the allowlist [1](#0-0) 

**Bypassed path — `handle_proposal_part()` in `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`:**

When a validator receives a `ProposalPart::Transactions` batch from the proposer, each `ConsensusTransaction` is converted directly:

```rust
let conversion_results =
    futures::future::join_all(txs.into_iter().map(|tx| {
        transaction_converter.convert_consensus_tx_to_internal_consensus_tx(tx)  // ← no permission check
    }))
    ...
let input = SendTxsForProposalInput { proposal_id, txs };
batcher.send_txs_for_proposal(input).await   // ← sent straight to blockifier
``` [2](#0-1) 

`convert_consensus_tx_to_internal_consensus_tx()` calls the shared private helper `convert_rpc_tx_to_internal()`, which performs hash calculation and class compilation but contains no call to `check_declare_permissions()`: [3](#0-2) [4](#0-3) 

The gateway is never consulted on the consensus path. The converted `InternalConsensusTransaction` is forwarded directly to the batcher and executed by the blockifier.

**The two asymmetric entry points mirror the PSM analog exactly:**

| Entry point | Calls `check_declare_permissions`? |
|---|---|
| `Gateway::add_tx_inner()` (user / P2P mempool) | **Yes** |
| `validate_proposal()` (consensus / proposer) | **No** |

**Production configuration confirms the controls are actively used:** [5](#0-4) [6](#0-5) 

`authorized_declarer_accounts` is described as "only these accounts can declare new contracts" — a network-wide invariant, not merely a local admission hint.

### Impact Explanation

When `authorized_declarer_accounts` is set to a non-empty allowlist, a Byzantine proposer can craft a `ProposalPart::Transactions` batch containing a `ConsensusTransaction::RpcTransaction(RpcDeclareTransaction::V3(...))` whose `sender_address` is not in the allowlist. Every honest validator will convert and forward the transaction to the batcher without checking the allowlist. The blockifier executes the declare, registering the class hash in the global state. The resulting state diverges from what the operator's admission policy permits — a wrong class hash value committed from execution logic on accepted input.

Matches: **High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing** (the gateway's declare permission invariant is broken for the consensus ingestion path), and potentially **Critical — wrong class hash / storage value from blockifier execution logic**.

### Likelihood Explanation

Medium. Requires a Byzantine validator node to act as proposer and craft a proposal with unauthorized declares. In a decentralized BFT network any validator rotates into the proposer role. The `authorized_declarer_accounts` allowlist is a production-deployed configuration (present in `replacer_gateway_config.json` as a replaceable parameter), making the precondition realistic.

### Recommendation

Apply `check_declare_permissions()` (or an equivalent check) consistently for all paths that process declare transactions. Two options:

1. **Enforce inside `convert_consensus_tx_to_internal_consensus_tx()`** — before converting a `ConsensusTransaction::RpcTransaction(RpcDeclareTransaction::V3(...))`, verify the sender is authorized and declares are not blocked. This makes the converter self-contained and prevents future entry points from re-introducing the bypass.

2. **Validate at the consensus entry point** — in `handle_proposal_part()`, before calling `send_txs_for_proposal()`, check each converted declare against the gateway's declare permission config and reject the proposal if any declare violates it.

### Proof of Concept

1. Configure a validator node with `authorized_declarer_accounts = ["0x1"]` (only address `0x1` may declare).
2. A Byzantine proposer crafts a `ProposalPart::Transactions` batch containing a `ConsensusTransaction::RpcTransaction(RpcDeclareTransaction::V3 { sender_address: 0x2, ... })`.
3. The validator's `validate_proposal()` receives the batch and calls `convert_consensus_tx_to_internal_consensus_tx()` — no permission check is performed.
4. The converted `InternalConsensusTransaction` is forwarded to `batcher.send_txs_for_proposal()`.
5. The blockifier executes the declare; the class hash from unauthorized address `0x2` is committed to the state.
6. The validator's own gateway would have rejected this transaction at step (3) of `add_tx_inner()` with `UnauthorizedDeclare`, but the consensus path never reaches that code. [7](#0-6) [1](#0-0) [2](#0-1) [3](#0-2) [8](#0-7)

### Citations

**File:** crates/apollo_gateway/src/gateway.rs (L228-233)
```rust
        if let RpcTransaction::Declare(ref declare_tx) = tx {
            if let Err(e) = self.check_declare_permissions(declare_tx) {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        }
```

**File:** crates/apollo_gateway/src/gateway.rs (L407-433)
```rust
    fn check_declare_permissions(
        &self,
        declare_tx: &RpcDeclareTransaction,
    ) -> Result<(), StarknetError> {
        // TODO(noamsp): Return same error as in Python gateway.
        if self.config.static_config.block_declare {
            return Err(StarknetError {
                code: StarknetErrorCode::UnknownErrorCode(
                    "StarknetErrorCode.BLOCKED_TRANSACTION_TYPE".to_string(),
                ),
                message: "Transaction type is temporarily blocked.".to_string(),
            });
        }
        let RpcDeclareTransaction::V3(declare_v3_tx) = declare_tx;
        if !self.config.is_authorized_declarer(&declare_v3_tx.sender_address) {
            return Err(StarknetError {
                code: StarknetErrorCode::KnownErrorCode(
                    KnownStarknetErrorCode::UnauthorizedDeclare,
                ),
                message: format!(
                    "Account address {} is not allowed to declare contracts.",
                    &declare_v3_tx.sender_address
                ),
            });
        }
        Ok(())
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L598-646)
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

**File:** crates/apollo_deployments/resources/app_configs/replacer_gateway_config.json (L1-4)
```json
{
  "gateway_config.static_config.authorized_declarer_accounts": "$$$_GATEWAY_CONFIG-STATIC_CONFIG-AUTHORIZED_DECLARER_ACCOUNTS_$$$",
  "gateway_config.static_config.authorized_declarer_accounts.#is_none": "$$$_GATEWAY_CONFIG-STATIC_CONFIG-AUTHORIZED_DECLARER_ACCOUNTS-IS_NONE_$$$",
  "gateway_config.static_config.block_declare": false,
```

**File:** crates/apollo_gateway_config/src/config.rs (L40-58)
```rust
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, Validate)]
pub struct GatewayStaticConfig {
    #[validate(nested)]
    pub stateless_tx_validator_config: StatelessTransactionValidatorConfig,
    #[validate(nested)]
    pub stateful_tx_validator_config: StatefulTransactionValidatorConfig,
    #[validate(nested)]
    pub contract_class_manager_config: ContractClassManagerConfig,
    pub chain_info: ChainInfo,
    pub block_declare: bool,
    #[serde(default, deserialize_with = "deserialize_comma_separated_str")]
    pub authorized_declarer_accounts: Option<Vec<ContractAddress>>,
    /// Maximum number of Sierra-to-CASM compilations (triggered by declare transactions) allowed
    /// to run concurrently. Declares that arrive while this limit is reached are rejected
    /// immediately rather than queued.
    #[validate(range(min = 1))]
    pub max_concurrent_declare_compilations: usize,
    pub proof_archive_writer_config: ProofArchiveWriterConfig,
}
```

**File:** crates/apollo_gateway_config/src/config.rs (L140-147)
```rust
impl GatewayConfig {
    pub fn is_authorized_declarer(&self, declarer_address: &ContractAddress) -> bool {
        match &self.static_config.authorized_declarer_accounts {
            Some(allowed_accounts) => allowed_accounts.contains(declarer_address),
            None => true,
        }
    }
}
```

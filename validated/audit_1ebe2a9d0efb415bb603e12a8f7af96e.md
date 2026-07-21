### Title
Gateway `authorized_declarer_accounts` / `block_declare` Admission Controls Not Enforced on Consensus Conversion Path, Allowing Unauthorized Declare Transactions to Be Executed — (`crates/apollo_gateway/src/gateway.rs`, `crates/apollo_transaction_converter/src/transaction_converter.rs`)

---

### Summary

The gateway enforces a sender-address whitelist (`authorized_declarer_accounts`) and a global kill-switch (`block_declare`) for `Declare` transactions on the RPC admission path. These checks are absent from the consensus validation path. A malicious proposer can craft a `ConsensusTransaction::RpcTransaction(Declare(...))` from an unauthorized sender address and include it in a block proposal. Validator nodes will convert and forward it to the batcher without any permission check, causing the blockifier to execute the declaration and commit an unauthorized class hash to chain state.

---

### Finding Description

**Gateway path (enforces controls):**

In `GenericGateway::add_tx_inner`, before any conversion, the gateway calls `check_declare_permissions`:

```rust
// crates/apollo_gateway/src/gateway.rs:228-233
if let RpcTransaction::Declare(ref declare_tx) = tx {
    if let Err(e) = self.check_declare_permissions(declare_tx) {
        metric_counters.record_add_tx_failure(&e);
        return Err(e);
    }
}
```

`check_declare_permissions` enforces two controls:

```rust
// crates/apollo_gateway/src/gateway.rs:407-433
fn check_declare_permissions(&self, declare_tx: &RpcDeclareTransaction) -> Result<(), StarknetError> {
    if self.config.static_config.block_declare {
        return Err(...); // global kill-switch
    }
    if !self.config.is_authorized_declarer(&declare_v3_tx.sender_address) {
        return Err(...); // sender whitelist
    }
    Ok(())
}
```

`is_authorized_declarer` returns `false` for any address not in `authorized_declarer_accounts` when the list is `Some(...)`:

```rust
// crates/apollo_gateway_config/src/config.rs:141-146
pub fn is_authorized_declarer(&self, declarer_address: &ContractAddress) -> bool {
    match &self.static_config.authorized_declarer_accounts {
        Some(allowed_accounts) => allowed_accounts.contains(declarer_address),
        None => true,
    }
}
```

**Consensus path (no controls):**

When a validator receives a `ProposalPart::Transactions` batch, `handle_proposal_part` calls:

```rust
// crates/apollo_consensus_orchestrator/src/validate_proposal.rs:602-604
transaction_converter.convert_consensus_tx_to_internal_consensus_tx(tx)
```

`convert_consensus_tx_to_internal_consensus_tx` delegates directly to `convert_rpc_tx_to_internal`:

```rust
// crates/apollo_transaction_converter/src/transaction_converter.rs:184-202
async fn convert_consensus_tx_to_internal_consensus_tx(&self, tx: ConsensusTransaction) -> ... {
    match tx {
        ConsensusTransaction::RpcTransaction(tx) => {
            let (internal_tx, proof_data) = self.convert_rpc_tx_to_internal(tx).await?;
            ...
        }
        ...
    }
}
```

`convert_rpc_tx_to_internal` performs only class-hash compilation and hash calculation — **no call to `check_declare_permissions`, no `block_declare` check, no `authorized_declarer_accounts` check**:

```rust
// crates/apollo_transaction_converter/src/transaction_converter.rs:334-393
async fn convert_rpc_tx_to_internal(&self, tx: RpcTransaction) -> ... {
    let (tx_without_hash, proof_data) = match tx {
        RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => {
            let ClassHashes { class_hash, executable_class_hash_v2 } =
                self.class_manager_client.add_class(tx.contract_class).await?;
            // compiled_class_hash mismatch check only — no sender whitelist check
            ...
        }
        ...
    };
    let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
    Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
}
```

The resulting `InternalConsensusTransaction` is forwarded to the batcher via `send_txs_for_proposal`, which executes it through the blockifier. The blockifier has no `authorized_declarer_accounts` check; it only validates nonce, fees, and account signature. The unauthorized class declaration is committed to chain state.

---

### Impact Explanation

When `authorized_declarer_accounts` is configured (e.g., to restrict class declarations to a set of trusted deployers) or `block_declare = true` is set as an emergency measure, a malicious proposer can bypass both controls entirely by injecting a `Declare` transaction directly into a consensus proposal. Validators accept and execute it, writing an unauthorized `class_hash` → `compiled_class_hash` mapping into the Patricia Merkle Tree. This is a wrong state outcome: an unauthorized class hash is committed to chain state, contradicting the operator's explicit access-control policy.

This maps to: **Critical — Wrong state / class hash from blockifier/execution logic for accepted input.**

---

### Likelihood Explanation

The trigger requires a malicious consensus proposer — a legitimate validator-set participant who constructs a block proposal containing an unauthorized `Declare` transaction. In a decentralized sequencer with multiple validators, any one of them can be the proposer for a given round. The attack requires no special privileges beyond being a proposer, no invalid bytes (the transaction is structurally valid), and no external dependencies. The `authorized_declarer_accounts` feature is explicitly deployed in production configuration (`replacer_gateway_config.json` exposes it as a configurable field), making the control meaningful and the bypass impactful.

---

### Recommendation

The `check_declare_permissions` logic (both `block_declare` and `authorized_declarer_accounts`) must be enforced on the consensus conversion path, not only on the RPC gateway path. One approach: move the permission check into `convert_rpc_tx_to_internal` (or a wrapper called by both paths), or add an explicit check in `handle_proposal_part` before forwarding `Declare` transactions to the batcher. Alternatively, if these controls are intentionally gateway-only (i.e., the proposer is trusted to have already filtered transactions through its own gateway), this assumption must be documented and the threat model updated to reflect that a malicious proposer can bypass them.

---

### Proof of Concept

1. Operator configures `authorized_declarer_accounts = Some([0x1])` — only address `0x1` may declare.
2. Attacker controls a validator node that becomes the proposer for round R.
3. Attacker constructs a `ConsensusTransaction::RpcTransaction(RpcInvokeTransaction::V3(...))` — actually a `RpcDeclareTransaction::V3` — with `sender_address = 0x2` (not in the whitelist) and a valid Sierra class.
4. Attacker includes this transaction in the `ProposalPart::Transactions` batch sent to peer validators.
5. Each validator calls `convert_consensus_tx_to_internal_consensus_tx` → `convert_rpc_tx_to_internal` → no `check_declare_permissions` call → `InternalConsensusTransaction::RpcTransaction(InternalRpcDeclareTransactionV3 { sender_address: 0x2, ... })` is produced.
6. The internal transaction is forwarded to the batcher via `send_txs_for_proposal`.
7. The blockifier executes the declare: account validation passes (the account at `0x2` signs correctly), the class is stored.
8. `decision_reached` commits the block; the unauthorized class hash is now in chain state.
9. The gateway's `authorized_declarer_accounts` control has been completely bypassed. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** crates/apollo_gateway_config/src/config.rs (L49-51)
```rust
    pub block_declare: bool,
    #[serde(default, deserialize_with = "deserialize_comma_separated_str")]
    pub authorized_declarer_accounts: Option<Vec<ContractAddress>>,
```

**File:** crates/apollo_gateway_config/src/config.rs (L141-146)
```rust
    pub fn is_authorized_declarer(&self, declarer_address: &ContractAddress) -> bool {
        match &self.static_config.authorized_declarer_accounts {
            Some(allowed_accounts) => allowed_accounts.contains(declarer_address),
            None => true,
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

### Title
`allow_client_side_proving` Config Gate Enforced Only in Gateway Path, Bypassed Entirely in Consensus Validation Path — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

The `allow_client_side_proving` configuration flag is checked in the gateway's stateless validator to reject client-side proving (proof-carrying) transactions when the feature is disabled. However, the consensus proposal-validation path that processes peer-proposed transactions never invokes this check. A node configured with `allow_client_side_proving = false` will silently accept and execute proof-carrying `InvokeV3` transactions that arrive via a block proposal from any peer sequencer, directly contradicting the operator's stated policy.

### Finding Description

The gateway enforces the feature gate in `StatelessTransactionValidator::validate()`:

```rust
// crates/apollo_gateway/src/stateless_transaction_validator.rs
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

`validate()` is called inside `add_tx_inner` for every user-submitted transaction: [2](#0-1) 

The consensus validation path in `validate_proposal.rs` processes peer-proposed transactions by calling `convert_consensus_tx_to_internal_consensus_tx` directly: [3](#0-2) 

`convert_consensus_tx_to_internal_consensus_tx` calls `convert_rpc_tx_to_internal`, which computes the transaction hash and builds the internal representation, but **never invokes `validate_client_side_proving_allowed` or any equivalent check**: [4](#0-3) 

The `StatelessTransactionValidator` is not instantiated or referenced anywhere in the consensus orchestrator. The `allow_client_side_proving` field lives exclusively in `StatelessTransactionValidatorConfig`, which is part of the gateway's static config and is never consulted during consensus validation: [5](#0-4) 

### Impact Explanation

A validator node whose operator has set `allow_client_side_proving = false` will:

1. Correctly reject proof-carrying `InvokeV3` transactions submitted directly by users through the HTTP gateway.
2. Silently accept and execute identical proof-carrying transactions when they arrive inside a block proposal from any peer sequencer.

The blockifier's `validate_proof_facts` still runs and checks proof content (program hash, config hash, block hash), but it does **not** consult `allow_client_side_proving`. The operator's intent — "this node must not process client-side proving transactions" — is therefore violated for the entire consensus ingestion path. The divergent value is the `allow_client_side_proving` boolean: it is `false` in the gateway config but is never read during consensus validation, so the two components operate under different effective policies for the same transaction type.

This matches: **High — Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing**, and also the config-boundary audit pivot: one component (gateway) enforces the flag while another (consensus validator) commits the transaction without it.

### Likelihood Explanation

Any peer sequencer that has `allow_client_side_proving = true` on its own node will naturally include proof-carrying transactions in its proposals. A validator with the flag set to `false` will receive and finalize those proposals without any rejection. No malicious intent is required; a simple configuration mismatch between two honest nodes is sufficient to trigger the bypass.

### Recommendation

Apply the `allow_client_side_proving` check inside the consensus transaction-conversion path. The cleanest fix is to add a stateless validation step in `handle_proposal_part` (in `validate_proposal.rs`) before forwarding transactions to the batcher, mirroring the check already present in `add_tx_inner`. Alternatively, move the check into `convert_rpc_tx_to_internal` so it is enforced regardless of the ingestion path, or expose the flag through a shared validation utility callable from both the gateway and the consensus orchestrator.

### Proof of Concept

1. Configure a validator node with `allow_client_side_proving = false` in its gateway static config.
2. Have a peer sequencer (proposer) with `allow_client_side_proving = true` submit and include a valid `InvokeV3` transaction carrying non-empty `proof_facts` and `proof` fields.
3. The proposer broadcasts the block proposal over P2P.
4. The validator's `handle_proposal_part` in `validate_proposal.rs` calls `convert_consensus_tx_to_internal_consensus_tx` for the proof-carrying transaction.
5. `convert_rpc_tx_to_internal` succeeds — no call to `validate_client_side_proving_allowed` is made.
6. The transaction is forwarded to the batcher via `send_txs_for_proposal` and executed by the blockifier.
7. The validator has accepted and finalized a transaction type its operator explicitly disabled, with no error or log entry indicating the policy violation.

### Citations

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

**File:** crates/apollo_gateway/src/gateway.rs (L235-237)
```rust
        // Perform stateless validations.
        self.stateless_tx_validator.validate(&tx)?;

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

**File:** crates/apollo_gateway_config/src/config.rs (L123-147)
```rust
#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize, Validate)]
pub struct GatewayConfig {
    #[validate(nested)]
    pub dynamic_config: GatewayDynamicConfig,
    #[validate(nested)]
    pub static_config: GatewayStaticConfig,
}

impl SerializeConfig for GatewayConfig {
    fn dump(&self) -> BTreeMap<ParamPath, SerializedParam> {
        let mut dump = BTreeMap::new();
        dump.extend(prepend_sub_config_name(self.dynamic_config.dump(), "dynamic_config"));
        dump.extend(prepend_sub_config_name(self.static_config.dump(), "static_config"));
        dump
    }
}

impl GatewayConfig {
    pub fn is_authorized_declarer(&self, declarer_address: &ContractAddress) -> bool {
        match &self.static_config.authorized_declarer_accounts {
            Some(allowed_accounts) => allowed_accounts.contains(declarer_address),
            None => true,
        }
    }
}
```

### Title
Missing resource-bounds validation in consensus-path transaction conversion allows zero-fee transactions to be sequenced — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

The gateway path enforces that every incoming `RpcTransaction` carries non-zero resource bounds via `StatelessTransactionValidator::validate_resource_bounds`. The consensus/P2P path that processes `ConsensusTransaction` batches inside `handle_proposal_part` calls `convert_consensus_tx_to_internal_consensus_tx` and then forwards the result directly to the batcher — with **no equivalent resource-bounds check**. A Byzantine proposer can craft a `ConsensusTransaction` whose `AllResourceBounds` are entirely zero, have it accepted by every validator node, and cause the blockifier to execute it with `enforce_fee = false`, producing wrong fee-token-balance state and wrong receipts.

### Finding Description

**Gateway path (protected):**

`add_tx_inner` → `stateless_tx_validator.validate(&tx)` → `validate_resource_bounds` [1](#0-0) 

The check computes `ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO)` and rejects the transaction if it equals `Fee(0)`. It is gated by `self.config.validate_resource_bounds`, but when that flag is `true` (normal production operation) it is a hard gate. [2](#0-1) 

**Consensus path (unprotected):**

`handle_proposal_part` (in `validate_proposal.rs`) receives a `ProposalPart::Transactions` batch and calls: [3](#0-2) 

`convert_consensus_tx_to_internal_consensus_tx` delegates to the private `convert_rpc_tx_to_internal`: [4](#0-3) 

`convert_rpc_tx_to_internal` performs only a compiled-class-hash check for Declare transactions and a contract-address derivation for DeployAccount. It performs **no resource-bounds check** for any transaction type: [5](#0-4) 

The resulting `InternalConsensusTransaction` is forwarded to the batcher via `send_txs_for_proposal` without any intervening validation.

**Blockifier consequence:**

`enforce_fee` is derived from whether any resource bound is positive:

```
expected_enforce_fee = l1_gas_bound + l1_data_gas_bound + l2_gas_bound > 0
```

When all bounds are zero, `enforce_fee = false`, the pre-validation fee check (`check_fee_bounds`) is skipped, and the transaction executes without paying fees. The fee-token balance is not decremented, producing wrong committed state.

**Config pointer confirms the asymmetry:**

The shared `validate_resource_bounds` pointer target wires the flag into the gateway's stateless validator, the gateway's stateful validator, and the mempool — but **not** into the consensus conversion path, which has no such flag at all: [6](#0-5) 

### Impact Explanation

A Byzantine proposer (any validator node selected for a round) can include `ConsensusTransaction::RpcTransaction` entries with `AllResourceBounds { l1_gas: 0, l2_gas: 0, l1_data_gas: 0 }`. Every honest validator node will:

1. Accept the conversion without error.
2. Forward the transaction to the batcher.
3. Execute it with `enforce_fee = false`.
4. Commit a block whose receipts show zero fee and whose fee-token contract state is not decremented.

This satisfies **Critical — Wrong state, receipt, and incorrect fee/balance with economic impact**.

### Likelihood Explanation

Any validator that wins a proposal round can trigger this. No special key material beyond normal validator participation is required. The proposer constructs the `ProposalPart::Transactions` payload directly; there is no gateway or mempool on the receiving validator nodes that re-validates resource bounds for consensus-path transactions.

### Recommendation

Add a resource-bounds check inside `convert_consensus_tx_to_internal_consensus_tx` (or in `handle_proposal_part` before forwarding to the batcher) that mirrors the gateway's `validate_resource_bounds` logic:

```rust
// In convert_rpc_tx_to_internal, after matching the tx variant:
if ValidResourceBounds::AllResources(resource_bounds)
    .max_possible_fee(Tip::ZERO) == Fee(0)
{
    return Err(TransactionConverterError::ZeroResourceBounds);
}
```

This should be unconditional (not gated by a bootstrap flag) on the consensus path, because a proposer is not a trusted source and the check is cheap.

### Proof of Concept

1. Byzantine proposer constructs:
   ```rust
   RpcInvokeTransactionV3 {
       resource_bounds: AllResourceBounds {
           l1_gas: ResourceBounds { max_amount: 0, max_price_per_unit: 0 },
           l2_gas: ResourceBounds { max_amount: 0, max_price_per_unit: 0 },
           l1_data_gas: ResourceBounds { max_amount: 0, max_price_per_unit: 0 },
       },
       // ... other fields
   }
   ```
2. Wraps it as `ConsensusTransaction::RpcTransaction(...)` and sends it in a `ProposalPart::Transactions` batch.
3. Honest validator node receives it; `convert_consensus_tx_to_internal_consensus_tx` succeeds — no resource-bounds check exists in `convert_rpc_tx_to_internal`. [7](#0-6) 
4. Transaction reaches the batcher and blockifier with `enforce_fee = false`.
5. Blockifier skips pre-validation fee checks and executes the transaction without charging fees.
6. Committed block contains wrong fee-token balance state and wrong receipts (fee = 0 for a transaction that consumed real L2 gas).

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-68)
```rust
    fn validate_resource_bounds(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if !self.config.validate_resource_bounds {
            return Ok(());
        }

        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L602-616)
```rust
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L334-392)
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
```

**File:** crates/apollo_node_config/src/node_config.rs (L158-168)
```rust
            ser_pointer_target_param(
                "validate_resource_bounds",
                &true,
                "Indicates that validations related to resource bounds are applied. \
                It should be set to false during a system bootstrap.",
            ),
            set_pointing_param_paths(&[
                "gateway_config.static_config.stateful_tx_validator_config.validate_resource_bounds",
                "gateway_config.static_config.stateless_tx_validator_config.validate_resource_bounds",
                "mempool_config.static_config.validate_resource_bounds",
            ]),
```

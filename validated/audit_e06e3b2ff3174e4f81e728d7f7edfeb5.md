### Title
Consensus Transaction Conversion Path Bypasses Gateway `validate_resource_bounds` and DA-Mode Checks, Allowing Fee-Underpriced and OS-Incompatible Transactions to Reach Execution - (`crates/apollo_transaction_converter/src/transaction_converter.rs`)

### Summary

The gateway path and the consensus validation path both funnel `RpcTransaction` through the same shared `convert_rpc_tx_to_internal()` helper, but only the gateway path first runs `StatelessTransactionValidator::validate()`. The consensus path (`convert_consensus_tx_to_internal_consensus_tx`) calls `convert_rpc_tx_to_internal()` directly, skipping every stateless check. A malicious proposer can therefore inject transactions that carry zero resource bounds, a gas price below `min_gas_price`, non-L1 DA modes, or non-empty `paymaster_data`/`account_deployment_data` — all of which the gateway would reject — and have them accepted, converted, and forwarded to the batcher for execution.

### Finding Description

**Gateway path** (normal admission):

1. `GenericGateway::add_tx` calls `StatelessTransactionValidator::validate()` [1](#0-0) 
2. That validator enforces, among other things:
   - `validate_resource_bounds` — rejects zero-fee bounds and gas price below `min_gas_price` [2](#0-1) 
   - `validate_nonce_data_availability_mode` / `validate_fee_data_availability_mode` — enforces `DataAvailabilityMode::L1` [3](#0-2) 
   - `validate_empty_paymaster_data` / `validate_empty_account_deployment_data` — enforces empty fields [4](#0-3) 
3. Only after all checks pass does it call `convert_rpc_tx_to_internal_rpc_tx()`. [5](#0-4) 

**Consensus validation path** (proposal reception):

1. `validate_proposal` receives a `TransactionBatch` from the network and immediately calls `convert_consensus_tx_to_internal_consensus_tx()` for each transaction. [6](#0-5) 
2. `convert_consensus_tx_to_internal_consensus_tx` calls the shared private helper `convert_rpc_tx_to_internal()` directly — **no `StatelessTransactionValidator::validate()` is invoked**. [7](#0-6) 
3. `convert_rpc_tx_to_internal()` only computes the tx hash and extracts proof data; it performs none of the stateless checks. [8](#0-7) 

The two paths share the same conversion logic but diverge on validation: the gateway enforces it, the consensus path does not.

**Specific bypasses and their consequences:**

| Check bypassed | Gateway enforcement | Consequence when bypassed via consensus |
|---|---|---|
| `validate_resource_bounds` | Rejects zero-fee bounds; rejects `l2_gas.max_price_per_unit < min_gas_price` | Transaction reaches blockifier; if `enforce_fee()` returns `false` for zero bounds, `check_fee_bounds` and `verify_can_pay_committed_bounds` are skipped entirely in `perform_pre_validation_stage` |
| `validate_nonce_data_availability_mode` / `validate_fee_data_availability_mode` | Enforces `DataAvailabilityMode::L1` | OS Cairo code asserts L1 DA mode; block proof fails, causing liveness loss |
| `validate_empty_paymaster_data` / `validate_empty_account_deployment_data` | Enforces empty fields | OS Cairo code asserts `account_deployment_data_size = 0`; proof generation fails |

The blockifier's own pre-validation gating on `charge_fee`:

```rust
if self.execution_flags.charge_fee {
    self.check_fee_bounds(tx_context)?;
    verify_can_pay_committed_bounds(state, tx_context).map_err(Box::new)?;
}
``` [9](#0-8) 

means that if `charge_fee` is `false` (which `enforce_fee()` can produce for zero-bound transactions), the fee accounting checks are entirely skipped at execution time.

### Impact Explanation

A malicious proposer can craft `ConsensusTransaction::RpcTransaction` payloads with:
- **Zero `l2_gas.max_price_per_unit`**: bypasses `validate_resource_bounds`; if `enforce_fee()` returns `false`, the blockifier executes the transaction without charging fees — **incorrect fee/gas accounting with direct economic impact**.
- **`DataAvailabilityMode::L2` for nonce or fee**: bypasses DA-mode checks; the OS rejects the block during proof generation — **liveness failure**.
- **Non-empty `paymaster_data` or `account_deployment_data`**: bypasses emptiness checks; the OS Cairo assertion `assert account_deployment_data_size = 0` fires — **proof generation failure**.

The first case maps to **Critical: Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact**. The latter two map to a liveness/DoS impact on the sequencer.

### Likelihood Explanation

Exploitation requires control of a proposer node (a consensus participant). This is analogous to the external report's "malicious observer" — a somewhat-trusted but not fully-trusted role in a decentralized system. The proposer rotates each round, so any compromised consensus node can trigger this during its proposer turn. No cryptographic forgery is required; the attacker simply constructs a valid-looking `ConsensusTransaction` with crafted resource bounds and broadcasts it as part of a proposal.

### Recommendation

Apply the same `StatelessTransactionValidator::validate()` checks to transactions arriving through the consensus path before they are forwarded to the batcher. The cleanest fix mirrors the external report's long-term recommendation: share the validation code between the two paths. Concretely, inside `convert_consensus_tx_to_internal_consensus_tx` (or in `validate_proposal`'s `handle_proposal_part` before calling the converter), call `StatelessTransactionValidator::validate()` on each `RpcTransaction` before conversion. This ensures that resource bounds, DA modes, and field-emptiness invariants are enforced regardless of which admission path a transaction takes.

### Proof of Concept

```
1. Construct an RpcInvokeTransactionV3 with:
     resource_bounds.l2_gas.max_price_per_unit = 0   // zero gas price
     resource_bounds.l2_gas.max_amount = 0            // zero gas amount
   (all other fields valid)

2. Wrap it as ConsensusTransaction::RpcTransaction and broadcast it
   in a ProposalPart::Transactions batch from a proposer node.

3. Validator nodes receive the batch in validate_proposal →
   handle_proposal_part → convert_consensus_tx_to_internal_consensus_tx.
   No StatelessTransactionValidator::validate() is called.
   convert_rpc_tx_to_internal() succeeds and computes a valid tx_hash.

4. The InternalConsensusTransaction is forwarded to the batcher via
   send_txs_for_proposal. The batcher executes it.

5. If enforce_fee() returns false for zero resource bounds, the blockifier's
   perform_pre_validation_stage skips check_fee_bounds and
   verify_can_pay_committed_bounds, executing the transaction fee-free.

6. Compare with the gateway path: submitting the same transaction via
   add_tx would be rejected at step 1 with
   StatelessTransactionValidatorError::ZeroResourceBounds.
```

The divergence is directly observable: the gateway rejects the transaction at `validate_resource_bounds` [2](#0-1) , while the consensus path accepts it at `convert_rpc_tx_to_internal` [8](#0-7)  and forwards it to the batcher without any resource-bounds check.

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L33-54)
```rust
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-88)
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
        }

        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
        }

        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }

        Ok(())
    }
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L199-229)
```rust
    fn validate_nonce_data_availability_mode(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let expected_da_mode = DataAvailabilityMode::L1;
        let da_mode = *tx.nonce_data_availability_mode();
        if da_mode != expected_da_mode {
            return Err(StatelessTransactionValidatorError::InvalidDataAvailabilityMode {
                field_name: "nonce".to_string(),
            });
        };

        Ok(())
    }

    /// The Starknet OS enforces that the fee data availability mode is L1. We add this validation
    /// here in the gateway to prevent transactions from failing the OS.
    fn validate_fee_data_availability_mode(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let expected_fee_mode = DataAvailabilityMode::L1;
        let fee_mode = *tx.fee_data_availability_mode();
        if fee_mode != expected_fee_mode {
            return Err(StatelessTransactionValidatorError::InvalidDataAvailabilityMode {
                field_name: "fee".to_string(),
            });
        };

        Ok(())
    }
```

**File:** crates/apollo_gateway/src/gateway.rs (L443-465)
```rust
        let (internal_tx, verification_handle) =
            self.transaction_converter.convert_rpc_tx_to_internal_rpc_tx(tx).await.map_err(
                |e| {
                    warn!("Failed to convert RPC transaction to internal RPC transaction: {}", e);
                    transaction_converter_err_to_deprecated_gw_err(tx_signature, e)
                },
            )?;

        // Await the verification task immediately.
        let proof_data = self
            .await_verification_task_and_extract_proof_data(verification_handle, tx_signature)
            .await?;

        let executable_tx = self
            .transaction_converter
            .convert_internal_rpc_tx_to_executable_tx(internal_tx.clone())
            .await
            .map_err(|e| {
                warn!("Failed to convert internal RPC transaction to executable transaction: {e}");
                transaction_converter_err_to_deprecated_gw_err(tx_signature, e)
            })?;

        Ok((internal_tx, executable_tx, proof_data))
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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L353-372)
```rust
    // Performs static checks before executing validation entry point.
    // Note that nonce is incremented during these checks.
    pub fn perform_pre_validation_stage<S: State + StateReader>(
        &self,
        state: &mut S,
        tx_context: &TransactionContext,
    ) -> TransactionPreValidationResult<()> {
        let tx_info = &tx_context.tx_info;
        Self::handle_nonce(state, tx_info, self.execution_flags.strict_nonce_check)?;

        if self.execution_flags.charge_fee {
            self.check_fee_bounds(tx_context)?;

            verify_can_pay_committed_bounds(state, tx_context).map_err(Box::new)?;
        }

        self.validate_proof_facts(&tx_context.block_context, state)?;

        Ok(())
    }
```

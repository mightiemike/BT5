### Title
OS-Required Field Constraints Enforced at Gateway Admission but Absent in Consensus Conversion Path — (File: `crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

The `StatelessTransactionValidator` enforces several OS-required field constraints at the gateway (RPC) admission path. The consensus conversion path (`convert_consensus_tx_to_internal_consensus_tx`) processes the same `RpcTransaction` payload but applies none of these checks. A malicious block proposer can craft transactions that violate OS-required invariants, inject them through the consensus path, have them accepted and executed by the blockifier, and produce receipts and state diffs that diverge from what the Starknet OS would produce.

### Finding Description

The gateway's `StatelessTransactionValidator::validate()` enforces four OS-required constraints, each explicitly documented as preventing OS-level failures:

1. `validate_nonce_data_availability_mode` — enforces `nonce_data_availability_mode == DataAvailabilityMode::L1`
2. `validate_fee_data_availability_mode` — enforces `fee_data_availability_mode == DataAvailabilityMode::L1`
3. `validate_empty_paymaster_data` — enforces `paymaster_data` is empty
4. `validate_empty_account_deployment_data` — enforces `account_deployment_data` is empty (for Invoke/Declare) [1](#0-0) 

Each of these is annotated with comments such as:

> "The Starknet OS enforces that the nonce data availability mode is L1. We add this validation here in the gateway to prevent transactions from failing the OS." [2](#0-1) [3](#0-2) 

The consensus path enters through `convert_consensus_tx_to_internal_consensus_tx`, which calls the shared private helper `convert_rpc_tx_to_internal`: [4](#0-3) 

`convert_rpc_tx_to_internal` performs only a `compiled_class_hash` check for Declare transactions and a contract address derivation for DeployAccount. It never calls `StatelessTransactionValidator::validate()` or any equivalent check for the four OS-required constraints: [5](#0-4) 

The blockifier's `perform_pre_validation_stage` also does not check these fields — it only handles nonce, fee bounds, fee payment, and proof facts: [6](#0-5) 

The consensus path is invoked during block proposal validation in `validate_proposal.rs`: [7](#0-6) 

This means a malicious proposer can include transactions with `nonce_data_availability_mode = L2`, `fee_data_availability_mode = L2`, non-empty `paymaster_data`, or non-empty `account_deployment_data`. These pass through `convert_consensus_tx_to_internal_consensus_tx` without rejection, are forwarded to the batcher, and executed by the blockifier. The blockifier produces state diffs and receipts for these transactions. The Starknet OS, which enforces these constraints, would reject the same transactions, creating a divergence between the committed blockifier state and what the OS can prove.

The protobuf deserialization layer faithfully preserves all these fields from the wire format, so a malicious proposer has full control over them: [8](#0-7) 

### Impact Explanation

A malicious block proposer (any validator) can inject transactions with OS-invalid field values through the consensus path. The validator node's blockifier executes them and commits state diffs and receipts. The Starknet OS, which enforces these constraints, would reject the same transactions. This produces wrong receipts and wrong committed state relative to what the OS can prove — matching the "Critical. Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input" impact. Additionally, the accepted-but-unprovable transactions match "Critical. Invalid or unauthorized Starknet transaction accepted through... paymaster, or account-deployment logic."

### Likelihood Explanation

Any consensus participant (validator) can act as a block proposer. No special privilege is required. The malicious proposer only needs to craft a `ConsensusTransaction` with the forbidden field values set, which is straightforward given the protobuf wire format accepts arbitrary values for these fields. The bypass is deterministic and requires no brute force.

### Recommendation

Apply the same OS-required field constraints in the consensus conversion path. The simplest fix is to call `StatelessTransactionValidator::validate()` (or an equivalent subset covering the four OS-required checks) inside `convert_rpc_tx_to_internal` before the transaction hash is computed, or to add a dedicated validation step in `convert_consensus_tx_to_internal_consensus_tx` for `ConsensusTransaction::RpcTransaction` variants. Specifically, the following checks must be present on both paths:

- `nonce_data_availability_mode == DataAvailabilityMode::L1`
- `fee_data_availability_mode == DataAvailabilityMode::L1`
- `paymaster_data.is_empty()`
- `account_deployment_data.is_empty()` (for Invoke and Declare)

### Proof of Concept

1. A malicious validator acts as block proposer.
2. It constructs an `RpcInvokeTransactionV3` with `nonce_data_availability_mode = DataAvailabilityMode::L2` (or non-empty `paymaster_data`), signs it, and wraps it in a `ConsensusTransaction::RpcTransaction`.
3. It serializes this to protobuf — the `DeclareV3Common`/`InvokeV3` protobuf converter faithfully encodes the L2 DA mode value.
4. The validator node receives the block proposal and calls `convert_consensus_tx_to_internal_consensus_tx`. This calls `convert_rpc_tx_to_internal`, which skips all stateless checks and computes the transaction hash with the L2 DA mode encoded.
5. The `InternalRpcTransaction` is forwarded to the batcher. The blockifier's `perform_pre_validation_stage` does not check DA modes or paymaster data, so execution proceeds.
6. The blockifier commits a state diff and receipt for this transaction.
7. The Starknet OS, which enforces `nonce_data_availability_mode == L1`, rejects the transaction, producing a divergent receipt and making the block unprovable — or producing wrong committed state relative to the OS execution.

The gateway path would have caught this at step 2 via `validate_nonce_data_availability_mode`, but the consensus path has no equivalent gate. [9](#0-8) [10](#0-9)

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L37-43)
```rust
        Self::validate_contract_address(tx)?;
        Self::validate_empty_account_deployment_data(tx)?;
        Self::validate_empty_paymaster_data(tx)?;
        self.validate_resource_bounds(tx)?;
        self.validate_tx_size(tx)?;
        self.validate_nonce_data_availability_mode(tx)?;
        self.validate_fee_data_availability_mode(tx)?;
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L120-140)
```rust
    /// The Starknet OS enforces that the paymaster data is empty. We add this validation here in
    /// the gateway to prevent transactions from failing the OS.
    fn validate_empty_paymaster_data(
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let paymaster_data = match tx {
            RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(tx)) => {
                &tx.paymaster_data
            }
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => &tx.paymaster_data,
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx)) => &tx.paymaster_data,
        };

        if paymaster_data.is_empty() {
            Ok(())
        } else {
            Err(StatelessTransactionValidatorError::NonEmptyField {
                field_name: "paymaster_data".to_string(),
            })
        }
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L197-229)
```rust
    /// The Starknet OS enforces that the nonce data availability mode is L1. We add this validation
    /// here in the gateway to prevent transactions from failing the OS.
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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L355-372)
```rust
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

**File:** crates/apollo_protobuf/src/transaction.rs (L66-95)
```rust
        let nonce_data_availability_mode =
            enum_int_to_volition_domain(value.nonce_data_availability_mode)?;

        let fee_data_availability_mode =
            enum_int_to_volition_domain(value.fee_data_availability_mode)?;

        let paymaster_data = PaymasterData(
            value.paymaster_data.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?,
        );

        let account_deployment_data = AccountDeploymentData(
            value
                .account_deployment_data
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?,
        );

        Ok(Self {
            resource_bounds,
            tip,
            signature,
            nonce,
            compiled_class_hash,
            sender_address,
            nonce_data_availability_mode,
            fee_data_availability_mode,
            paymaster_data,
            account_deployment_data,
        })
```

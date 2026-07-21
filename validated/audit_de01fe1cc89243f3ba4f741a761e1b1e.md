### Title
`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` silently downgrades `AllResources` to `L1Gas` in the p2p consensus path, causing conversion failure and proposal rejection for valid V3 invoke transactions - (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserialization of `ResourceBounds` into `ValidResourceBounds` (used in the state-sync/consensus p2p path) silently produces `ValidResourceBounds::L1Gas` whenever `l2_gas` and `l1_data_gas` are both zero. The RPC/mempool p2p path uses a separate, always-`AllResources` converter. A valid V3 invoke transaction accepted by the gateway with `AllResourceBounds { l2_gas: {0,0}, l1_data_gas: {0,0} }` is hashed under the `AllResources` domain (4-element Poseidon chain). When the proposer serializes it to protobuf and the validator deserializes it, the intermediate `InvokeTransactionV3` carries `L1Gas`, and the subsequent `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` hard-fails with `DEPRECATED_RESOURCE_BOUNDS_ERROR`. The validator marks the entire proposal as `Failed`, breaking consensus liveness.

---

### Finding Description

**Two divergent protobuf deserialization paths for `ResourceBounds`:**

`crates/apollo_protobuf/src/converters/rpc_transaction.rs` defines a direct `AllResourceBounds` converter that always succeeds and always produces `AllResources`:

```rust
impl TryFrom<protobuf::ResourceBounds> for AllResourceBounds { ... }
``` [1](#0-0) 

`crates/apollo_protobuf/src/converters/transaction.rs` defines a `ValidResourceBounds` converter that **silently downgrades** to `L1Gas` when both `l2_gas` and `l1_data_gas` are zero:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(...)
})
``` [2](#0-1) 

**The p2p consensus path uses the wrong converter:**

`TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` routes through `InvokeTransactionV3` (which uses `ValidResourceBounds`), not through `AllResourceBounds` directly:

```rust
let snapi_invoke: InvokeTransactionV3 = value.invoke...try_into()?;
// This conversion can fail only if the resource_bounds are not AllResources.
Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
``` [3](#0-2) 

The `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` hard-rejects anything that is not `AllResources`:

```rust
resource_bounds: match value.resource_bounds {
    ValidResourceBounds::AllResources(bounds) => bounds,
    _ => {
        return Err(StarknetApiError::OutOfRange { string: "resource_bounds".to_string() });
    }
},
``` [4](#0-3) 

**The gateway accepts the triggering transaction:**

The gateway's stateless validator only checks `l2_gas.max_price_per_unit >= min_gas_price` and that total fee > 0. When `min_gas_price = 0`, a transaction with `AllResourceBounds { l1_gas: {X, Y}, l2_gas: {0, 0}, l1_data_gas: {0, 0} }` (X·Y > 0) passes all checks: [5](#0-4) 

The hash is computed under the `AllResources` domain — a 4-element Poseidon chain including the `L1_DATA_GAS` element:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
``` [6](#0-5) 

**The proposer serializes `AllResources` correctly** — all three fields are present in the protobuf message with zero values for `l2_gas` and `l1_data_gas`: [7](#0-6) 

**The validator deserializes and fails** — the zero `l2_gas`/`l1_data_gas` fields trigger the `L1Gas` branch, the subsequent `RpcInvokeTransactionV3` conversion fails, and `validate_proposal` marks the proposal as `Failed`: [8](#0-7) 

---

### Impact Explanation

A valid transaction accepted by the gateway and included in a proposer's block causes the validator to reject the entire proposal with `DEPRECATED_RESOURCE_BOUNDS_ERROR`. This is a consensus liveness failure: the validator cannot accept a legitimately built proposal. The impact matches **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload**: the conversion logic in the p2p path incorrectly maps the transaction type from `AllResources` to `L1Gas`, producing a hard rejection instead of the correct `RpcInvokeTransactionV3`.

---

### Likelihood Explanation

The trigger requires `min_gas_price = 0` in the stateless validator config (so that `l2_gas = {0, 0}` passes admission) and a user who submits a V3 invoke with zero `l2_gas` and `l1_data_gas` bounds. The configuration is operator-controlled but not the default production value; however, the code path is unconditionally broken for any such transaction regardless of how it enters the system (including operator-constructed internal transactions as seen in `central_systest_blobs`). [9](#0-8) 

---

### Recommendation

Replace the two-step `InvokeTransactionV3` intermediate in `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` with a direct deserialization that uses `TryFrom<protobuf::ResourceBounds> for AllResourceBounds` (already defined in `rpc_transaction.rs`), bypassing the `ValidResourceBounds` path that can silently produce `L1Gas`. Alternatively, add a canonicalization step in `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` that always produces `AllResources` when all three fields are present in the protobuf message, regardless of their values.

---

### Proof of Concept

1. Set `gateway_config.static_config.stateless_tx_validator_config.min_gas_price = 0`.
2. Submit an invoke V3 transaction with `resource_bounds = AllResourceBounds { l1_gas: {max_amount: 1, max_price_per_unit: 1}, l2_gas: {0, 0}, l1_data_gas: {0, 0} }`.
3. Gateway accepts it; `convert_rpc_tx_to_internal` computes hash H using `AllResources` (4-element Poseidon chain with `L1_DATA_GAS` element). [10](#0-9) 
4. Transaction enters mempool and is included in a proposal by the batcher.
5. Proposer calls `convert_internal_consensus_tx_to_consensus_tx` → `RpcInvokeTransactionV3 { resource_bounds: AllResourceBounds{l2_gas:{0,0}, l1_data_gas:{0,0}} }` → serialized to protobuf with all three `ResourceBounds` fields present (zero values for l2/l1_data). [11](#0-10) 
6. Validator receives the protobuf `InvokeV3WithProof`. `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` fires `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas`. [12](#0-11) 
7. `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` sees `L1Gas`, returns `Err(OutOfRange)`, mapped to `DEPRECATED_RESOURCE_BOUNDS_ERROR`. [13](#0-12) 
8. `validate_proposal` receives the error and returns `HandledProposalPart::Failed(...)`, rejecting the entire proposal. [14](#0-13)

### Citations

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L115-131)
```rust
impl TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(mut value: protobuf::InvokeV3WithProof) -> Result<Self, Self::Error> {
        // Extract proof first, since `starknet_api::transaction::InvokeTransactionV3` does not
        // carry a `proof` field.
        let proof = Proof::from(std::mem::take(&mut value.proof));

        let snapi_invoke: InvokeTransactionV3 = value
            .invoke
            .ok_or(ProtobufConversionError::MissingField {
                field_description: "InvokeV3WithProof::invoke",
            })?
            .try_into()?;

        // This conversion can fail only if the resource_bounds are not AllResources.
        Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
    }
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L134-143)
```rust
impl From<RpcInvokeTransactionV3> for protobuf::InvokeV3WithProof {
    fn from(mut value: RpcInvokeTransactionV3) -> Self {
        // Extract proof first, since `starknet_api::transaction::InvokeTransactionV3` does not
        // carry a `proof` field.
        let proof = Arc::unwrap_or_clone(std::mem::take(&mut value.proof).0);

        let snapi_invoke: InvokeTransactionV3 = value.into();

        Self { invoke: Some(snapi_invoke.into()), proof }
    }
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L212-224)
```rust
impl TryFrom<protobuf::ResourceBounds> for AllResourceBounds {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        Ok(Self {
            l1_gas: value.l1_gas.ok_or(missing("ResourceBounds::l1_gas"))?.try_into()?,
            l2_gas: value.l2_gas.ok_or(missing("ResourceBounds::l2_gas"))?.try_into()?,
            l1_data_gas: value
                .l1_data_gas
                .ok_or(missing("ResourceBounds::l1_data_gas"))?
                .try_into()?,
        })
    }
}
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L417-437)
```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let Some(l1_gas) = value.l1_gas else {
            return Err(missing("ResourceBounds::l1_gas"));
        };
        let Some(l2_gas) = value.l2_gas else {
            return Err(missing("ResourceBounds::l2_gas"));
        };
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        let l1_gas: ResourceBounds = l1_gas.try_into()?;
        let l2_gas: ResourceBounds = l2_gas.try_into()?;
        let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
}
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L471-490)
```rust
impl From<ValidResourceBounds> for protobuf::ResourceBounds {
    fn from(value: ValidResourceBounds) -> Self {
        match value {
            ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
                l1_gas: Some(l1_gas.into()),
                l2_gas: Some(value.get_l2_bounds().into()),
                l1_data_gas: Some(ResourceBounds::default().into()),
            },
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => protobuf::ResourceBounds {
                l1_gas: Some(l1_gas.into()),
                l2_gas: Some(l2_gas.into()),
                l1_data_gas: Some(l1_data_gas.into()),
            },
        }
    }
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L589-598)
```rust
    fn try_from(value: InvokeTransactionV3) -> Result<Self, Self::Error> {
        Ok(Self {
            resource_bounds: match value.resource_bounds {
                ValidResourceBounds::AllResources(bounds) => bounds,
                _ => {
                    return Err(StarknetApiError::OutOfRange {
                        string: "resource_bounds".to_string(),
                    });
                }
            },
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

**File:** crates/starknet_api/src/transaction_hash.rs (L202-211)
```rust
    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L598-616)
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
```

**File:** crates/central_systest_blobs/src/cende_blob_regression_test.rs (L605-620)
```rust
        let rpc_tx_unsigned = InternalRpcInvokeTransactionV3 {
            sender_address: *OPERATOR_ADDRESS,
            calldata,
            signature: TransactionSignature::default(),
            resource_bounds,
            tip: Tip::default(),
            nonce,
            nonce_data_availability_mode: DataAvailabilityMode::L1,
            fee_data_availability_mode: DataAvailabilityMode::L1,
            account_deployment_data: AccountDeploymentData::default(),
            paymaster_data: PaymasterData::default(),
            proof_facts: ProofFacts::default(),
        };
        let tx_hash = rpc_tx_unsigned
            .calculate_transaction_hash(&CHAIN_ID, &TransactionVersion::THREE)
            .unwrap();
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L388-392)
```rust
                )
            }
        };
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

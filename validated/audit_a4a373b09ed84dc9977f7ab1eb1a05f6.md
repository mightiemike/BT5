### Title
`ValidResourceBounds` variant collapse in P2P protobuf deserialization silently rejects valid invoke transactions and produces divergent transaction hashes — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` collapses `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` into `L1Gas(l1_gas=X)` when both l2_gas and l1_data_gas are zero. The gateway accepts invoke V3 transactions carrying `AllResourceBounds` with zero l2/l1-data gas, computes their hash under the `AllResources` branch of `get_tip_resource_bounds_hash`, and stores them in the mempool. When those transactions are propagated via P2P mempool, the receiving node's protobuf round-trip collapses the variant to `L1Gas`, causing `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` to return `DEPRECATED_RESOURCE_BOUNDS_ERROR` and drop the transaction. The same collapse also produces a different `tip_resource_bounds_hash` value, so any node that does accept the transaction would bind it to a different hash than the originating node.

### Finding Description

**Step 1 — Gateway accepts the transaction.**

`StatelessTransactionValidator::validate_resource_bounds` checks only that `max_possible_fee > 0` and that `l2_gas.max_price_per_unit >= min_gas_price`. When `min_gas_price = 0` (or `validate_resource_bounds = false`), a transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` passes both checks. [1](#0-0) 

**Step 2 — Hash is computed under `AllResources` branch.**

`get_tip_resource_bounds_hash` appends the `l1_data_gas` element to the hash chain only for `AllResources`. For `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` the hash is `Poseidon(tip, concat(L1_GAS,X), concat(L2_GAS,0), concat(L1_DATA,0))`. [2](#0-1) 

**Step 3 — P2P serialization preserves all three bounds.**

`From<RpcInvokeTransactionV3> for protobuf::InvokeV3WithProof` converts through `InvokeTransactionV3` (which wraps the bounds as `ValidResourceBounds::AllResources`) and then through `From<ValidResourceBounds> for protobuf::ResourceBounds`, which emits all three `ResourceLimits` fields even when l2_gas and l1_data_gas are zero. [3](#0-2) [4](#0-3) 

**Step 4 — Deserialization collapses the variant.**

`ValidResourceBounds::try_from(protobuf::ResourceBounds)` returns `L1Gas` whenever `l2_gas.is_zero() && l1_data_gas.is_zero()`, regardless of whether the sender intended `AllResources`. [5](#0-4) 

**Step 5 — Receiving node rejects the transaction.**

`TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` goes through `InvokeTransactionV3` (now carrying `L1Gas`) and then calls `RpcInvokeTransactionV3::try_from(InvokeTransactionV3)`, which returns `Err(DEPRECATED_RESOURCE_BOUNDS_ERROR)` for any non-`AllResources` variant. The transaction is silently dropped. [6](#0-5) [7](#0-6) 

**Step 6 — Hash domain divergence.**

For `L1Gas(l1_gas=X)` the hash is `Poseidon(tip, concat(L1_GAS,X), concat(L2_GAS,0))` — two resource elements instead of three. Any node that does accept the transaction after the round-trip would compute a different `tx_hash` than the originating node stored in `InternalRpcTransaction.tx_hash`. The mempool protobuf propagation sets `transaction_hash: None`, so the receiving node must recompute the hash from scratch. [8](#0-7) [9](#0-8) 

### Impact Explanation

A valid invoke V3 transaction accepted by the gateway with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` cannot be propagated to any peer via the P2P mempool path. Every receiving node returns `DEPRECATED_RESOURCE_BOUNDS_ERROR` and discards it. If the originating node is not the sequencer, the transaction is permanently stuck and never sequenced. This matches: **High — Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

The secondary hash divergence also matches: **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

### Likelihood Explanation

- The trigger requires `min_gas_price = 0` (or `validate_resource_bounds = false`) in the gateway config. The test suite explicitly validates this configuration as correct (`valid_l1_gas` test case with `l2_gas = Default::default()`), so it is a supported and reachable configuration.
- Any unprivileged user can craft the transaction; no special privileges are needed.
- The `transaction_hash: None` in the mempool protobuf means every peer independently recomputes the hash, amplifying the divergence.

### Recommendation

1. **Fix the collapse in `ValidResourceBounds::try_from`**: do not infer the variant from zero values. Instead, use an explicit discriminant field in the protobuf schema (e.g., a `resource_bounds_version` enum), or always deserialize to `AllResources` when all three fields are present in the wire message.

2. **Add a gateway guard**: reject transactions where `resource_bounds` would round-trip to a different `ValidResourceBounds` variant. Concretely, reject `AllResourceBounds { l2_gas: 0, l1_data_gas: 0 }` at the gateway, or canonicalize them to `L1Gas` before hash computation.

3. **Align the hash function**: `get_tip_resource_bounds_hash` should produce the same output for `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` and `L1Gas(l1_gas=X)`, or the gateway must ensure these two representations never coexist for the same transaction.

### Proof of Concept

```
1. Craft RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
     }

2. Submit to gateway (min_gas_price = 0 config).
   → max_possible_fee = 1000 * 1 = 1000 > 0  ✓
   → l2_gas.max_price_per_unit (0) >= min_gas_price (0)  ✓
   → Gateway accepts; hash H_all computed with 3-element resource chain.

3. Gateway propagates via P2P mempool:
   RpcInvokeTransactionV3
     → InvokeTransactionV3 (AllResources)
     → protobuf::InvokeV3 (l2_gas=Some(0,0), l1_data_gas=Some(0,0))
     → protobuf::MempoolTransaction { transaction_hash: None }

4. Receiving peer deserializes:
   protobuf::InvokeV3
     → ValidResourceBounds::try_from: l2_gas.is_zero() && l1_data_gas.is_zero()
     → ValidResourceBounds::L1Gas(l1_gas)   ← variant collapsed
   InvokeTransactionV3 { resource_bounds: L1Gas(...) }
     → RpcInvokeTransactionV3::try_from: resource_bounds is not AllResources
     → Err(DEPRECATED_RESOURCE_BOUNDS_ERROR)   ← transaction dropped

5. Hash divergence (if peer somehow accepted):
   H_all = Poseidon(tip, concat(L1_GAS,X), concat(L2_GAS,0), concat(L1_DATA,0))
   H_l1  = Poseidon(tip, concat(L1_GAS,X), concat(L2_GAS,0))
   H_all ≠ H_l1
```

### Citations

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

**File:** crates/starknet_api/src/transaction_hash.rs (L187-211)
```rust
// An implementation of the SNIP: https://github.com/EvyatarO/SNIPs/blob/snip-8/SNIPS/snip-8.md
pub fn get_tip_resource_bounds_hash(
    resource_bounds: &ValidResourceBounds,
    tip: &Tip,
) -> Result<Felt, StarknetApiError> {
    let l1_resource_bounds = resource_bounds.get_l1_bounds();
    let l2_resource_bounds = resource_bounds.get_l2_bounds();

    // L1 and L2 gas bounds always exist.
    // Old V3 txs always have L2 gas bounds of zero, but they exist.
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];

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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L67-73)
```rust
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(txn)) => {
                protobuf::MempoolTransaction {
                    txn: Some(protobuf::mempool_transaction::Txn::InvokeV3(txn.into())),
                    // TODO(alonl): Consider removing transaction hash from protobuf
                    transaction_hash: None,
                }
            }
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L115-132)
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
}
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L134-144)
```rust
impl From<RpcInvokeTransactionV3> for protobuf::InvokeV3WithProof {
    fn from(mut value: RpcInvokeTransactionV3) -> Self {
        // Extract proof first, since `starknet_api::transaction::InvokeTransactionV3` does not
        // carry a `proof` field.
        let proof = Arc::unwrap_or_clone(std::mem::take(&mut value.proof).0);

        let snapi_invoke: InvokeTransactionV3 = value.into();

        Self { invoke: Some(snapi_invoke.into()), proof }
    }
}
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L417-436)
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
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L471-489)
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
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L124-141)
```rust
    pub fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
    ) -> Result<TransactionHash, StarknetApiError> {
        let transaction_version = &self.version();
        match self {
            InternalRpcTransactionWithoutTxHash::Declare(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
            InternalRpcTransactionWithoutTxHash::Invoke(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
            InternalRpcTransactionWithoutTxHash::DeployAccount(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
        }
    }
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L586-612)
```rust
impl TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3 {
    type Error = StarknetApiError;

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
            signature: value.signature,
            nonce: value.nonce,
            tip: value.tip,
            paymaster_data: value.paymaster_data,
            nonce_data_availability_mode: value.nonce_data_availability_mode,
            fee_data_availability_mode: value.fee_data_availability_mode,
            sender_address: value.sender_address,
            calldata: value.calldata,
            account_deployment_data: value.account_deployment_data,
            proof_facts: value.proof_facts,
            proof: Proof::default(),
        })
    }
}
```

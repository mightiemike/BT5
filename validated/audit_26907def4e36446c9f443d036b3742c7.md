### Title
Protobuf `ValidResourceBounds` Deserialization Selects Wrong Variant for Post-0.13.3 Transactions with Zero L2/Data-Gas Bounds, Breaking Consensus Round-Trip and Causing Block Rejection - (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter uses a **value-based heuristic** (`l1_data_gas.is_zero() && l2_gas.is_zero()`) to decide between the `L1Gas` and `AllResources` variants. A post-0.13.3 transaction whose `AllResourceBounds` has zero `l2_gas` and `l1_data_gas` (but non-zero `l1_gas`) is serialized as `AllResources` and deserialized back as `L1Gas`. Because `get_tip_resource_bounds_hash` hashes a different field set for each variant, the round-trip produces a different transaction hash, and the subsequent `RpcInvokeTransactionV3::try_from` conversion hard-rejects the `L1Gas` variant, causing the consensus block proposal to be rejected by every receiving peer.

---

### Finding Description

**Invariant broken:** Every post-0.13.3 V3 transaction carries `AllResourceBounds` and must always deserialize to `ValidResourceBounds::AllResources`. The variant must be determined by the transaction version, not by whether the resource-bound values happen to be zero.

**Root cause — wrong variant selection:** [1](#0-0) 

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← wrong for AllResources with zero values
} else {
    ValidResourceBounds::AllResources(...)
})
```

This mirrors the external bug exactly: a cumulative/aggregate value (`l2_gas + l1_data_gas == 0`) is used as a tier-boundary test instead of the correct discriminant (the transaction version / type tag).

**Serialization path (correct):**

`RpcInvokeTransactionV3 { resource_bounds: AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 } }` is converted to `InvokeTransactionV3` with `ValidResourceBounds::AllResources(...)`: [2](#0-1) 

then serialized to protobuf with `l2_gas: Some(0)` and `l1_data_gas: Some(0)` present: [3](#0-2) 

**Deserialization path (wrong):**

On the receiving peer, `l2_gas.is_zero() && l1_data_gas.is_zero()` is true, so the converter returns `ValidResourceBounds::L1Gas(l1_gas)`. The subsequent conversion to `RpcInvokeTransactionV3` then hard-fails: [4](#0-3) 

```rust
resource_bounds: match value.resource_bounds {
    ValidResourceBounds::AllResources(bounds) => bounds,
    _ => return Err(StarknetApiError::OutOfRange { ... }),  // ← triggered
},
```

**Hash divergence (even if the conversion were allowed):**

`get_tip_resource_bounds_hash` includes `l1_data_gas` in the Poseidon preimage only for `AllResources`; for `L1Gas` it is omitted entirely: [5](#0-4) 

So `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` and `L1Gas(X)` produce **different hashes** even though the numeric values are identical. The hash stored in `InternalRpcTransaction.tx_hash` (computed as `AllResources`) would not match any hash recomputed from the deserialized `L1Gas` form.

**The developers are aware of the symptom** but have only worked around it in tests, not fixed it in production: [6](#0-5) 

```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(...) {
    resource_bounds.l2_gas.max_amount = GasAmount(1);  // workaround
}
```

---

### Impact Explanation

**High — valid transaction rejected before sequencing / consensus block rejected.**

A proposer node includes a valid transaction (accepted by the gateway) in a block proposal. Every receiving peer fails to deserialize the consensus protobuf message for that transaction, causing the entire block proposal to be rejected. The proposer's round fails, consensus must elect a new proposer, and the transaction can never be sequenced. This matches the allowed impact: *"Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing"* and *"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."*

---

### Likelihood Explanation

**Low.** The trigger requires:
1. Gateway `min_gas_price = 0` (so `l2_gas.max_price_per_unit = 0` passes the gateway check at `crates/apollo_gateway/src/stateless_transaction_validator.rs` line 71).
2. A transaction submitted with `AllResourceBounds { l1_gas: non-zero, l2_gas: 0, l1_data_gas: 0 }`. [7](#0-6) 

Both conditions are reachable in a permissive deployment configuration.

---

### Recommendation

Replace the value-based heuristic with a version-aware or explicit-tag-based discriminant. Since all transactions entering the sequencer today are V3

### Citations

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L568-583)
```rust
impl From<RpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: RpcInvokeTransactionV3) -> Self {
        Self {
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            tip: tx.tip,
            signature: tx.signature,
            nonce: tx.nonce,
            sender_address: tx.sender_address,
            calldata: tx.calldata,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
            proof_facts: tx.proof_facts,
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

**File:** crates/starknet_api/src/transaction_hash.rs (L188-211)
```rust
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

**File:** crates/apollo_protobuf/src/converters/consensus_test.rs (L26-44)
```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(transactions: &mut [ConsensusTransaction]) {
    let transaction = &mut transactions[0];
    match transaction {
        ConsensusTransaction::RpcTransaction(rpc_transaction) => match rpc_transaction {
            RpcTransaction::Declare(RpcDeclareTransaction::V3(RpcDeclareTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::Invoke(RpcInvokeTransaction::V3(RpcInvokeTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(
                RpcDeployAccountTransactionV3 { resource_bounds, .. },
            )) => {
                resource_bounds.l2_gas.max_amount = GasAmount(1);
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

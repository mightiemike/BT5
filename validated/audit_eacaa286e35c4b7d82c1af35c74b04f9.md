### Title
`AllResourceBounds` with zero L2/L1-data gas survives gateway admission but is irrecoverably dropped by protobuf round-trip, causing P2P rejection of valid transactions and hash-domain divergence — (`crates/apollo_protobuf/src/converters/transaction.rs`, `crates/apollo_protobuf/src/converters/rpc_transaction.rs`, `crates/starknet_api/src/transaction_hash.rs`)

---

### Summary

The sequencer's `ValidResourceBounds` type has two variants: `L1Gas` (pre-0.13.3) and `AllResources` (0.13.3+). The gateway always accepts transactions as `AllResources` (since `RpcTransaction` uses `AllResourceBounds` directly). However, the protobuf deserializer silently re-classifies any `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` back to `L1Gas(X)` based purely on whether the numeric values are zero. The subsequent RPC-transaction protobuf converter then hard-rejects the `L1Gas` variant with `DEPRECATED_RESOURCE_BOUNDS_ERROR`. This creates an asymmetric split: the originating node accepts and hashes the transaction under the `AllResources` domain (3-element poseidon chain), while every P2P peer rejects it outright. Additionally, `get_tip_resource_bounds_hash` produces structurally different hashes for the two variants even when all numeric values are identical, so any node that did accept the re-classified form would bind a different hash than the one the user signed.

---

### Finding Description

**Step 1 – Gateway accepts the transaction.**

`StatelessTransactionValidator::validate_resource_bounds` in `crates/apollo_gateway/src/stateless_transaction_validator.rs` checks only that `max_possible_fee(Tip::ZERO) != 0`. A transaction with `AllResourceBounds { l1_gas: { max_amount: N, max_price: P }, l2_gas: 0, l1_data_gas: 0 }` passes this check as long as `N * P > 0`. [1](#0-0) 

**Step 2 – Hash is computed under the `AllResources` domain (3-element chain).**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` branches on the variant. For `AllResources` it appends `L1_DATA_GAS` to the poseidon chain; for `L1Gas` it does not. With identical numeric values the two variants produce different hashes. [2](#0-1) 

**Step 3 – Protobuf serialization preserves all three fields.**

`From<ValidResourceBounds> for protobuf::ResourceBounds` always emits all three fields, including `l1_data_gas: Some(ResourceBounds::default().into())` for the `L1Gas` variant and the actual (zero) value for `AllResources`. [3](#0-2) 

**Step 4 – Protobuf deserialization silently re-classifies to `L1Gas`.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` applies the heuristic: if `l1_data_gas.is_zero() && l2_gas.is_zero()` → `L1Gas`. A transaction originally submitted as `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is therefore re-classified to `L1Gas(X)` on every receiving peer. [4](#0-3) 

**Step 5 – RPC-transaction converter hard-rejects the `L1Gas` variant.**

`TryFrom<protobuf::DeclareV3WithClass> for RpcDeclareTransactionV3` (and the analogous invoke path) matches on `ValidResourceBounds::AllResources` and returns `DEPRECATED_RESOURCE_BOUNDS_ERROR` for any other variant. Because step 4 produced `L1Gas`, the conversion fails and the transaction is dropped. [5](#0-4) 

**The analog to the external bug.** In the Perennial vault, long and short products carry asymmetric maker fees; repeated rebalancing between them drains collateral. Here, `AllResources` and `L1Gas` are two representations of the same numeric resource bounds but with asymmetric hash domains and asymmetric protobuf round-trip behaviour. A transaction that "rebalances" across the gateway→P2P boundary loses its identity: the gateway commits it under the `AllResources` hash domain, but every P2P peer re-classifies it to `L1Gas` and rejects it, permanently preventing sequencing.

---

### Impact Explanation

A valid transaction accepted by the gateway and stored in the mempool is irrecoverably rejected by all P2P peers. The block proposer cannot include it in a consensus proposal that other validators will accept. The transaction is silently lost despite passing all gateway-side validation. This matches the **High** impact scope: *"Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."*

A secondary consequence is a hash-domain split: if any code path were to accept the re-classified `L1Gas` form, the recomputed hash would differ from the hash the user signed (different poseidon preimage length), causing account-validation to fail — matching the **Critical** scope: *"Invalid or unauthorized Starknet transaction accepted through account validation, signature … logic."*

---

### Likelihood Explanation

The trigger condition is a transaction with `AllResourceBounds` where `l2_gas = 0` and `l1_data_gas = 0` (only L1 gas is non-zero). This is a natural configuration for users who have not yet migrated to the full three-resource model. The gateway's stateless validator explicitly allows it (only one non-zero resource bound is required). The failure is deterministic and reproducible for any such transaction.

---

### Recommendation

1. **Fix the protobuf deserializer.** Remove the `L1Gas` re-classification heuristic from `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. Transactions arriving via P2P that were originally submitted as `AllResources` should remain `AllResources` regardless of whether the numeric values of `l2_gas` and `l1_data_gas` are zero. The variant should be determined by the protocol version / transaction version, not by inspecting field values.

2. **Canonicalize the hash domain at submission time.** The gateway should record which hash domain was used (i.e., which `ValidResourceBounds` variant) and propagate that information through the P2P message so that receiving nodes do not need to infer it from field values.

3. **Add a round-trip test.** A property test that serializes an `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` transaction to protobuf and back, asserting that the variant and hash are preserved, would have caught this.

---

### Proof of Concept

```
1. Construct RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     }

2. Submit to gateway → accepted (max_possible_fee = 1000 > 0).

3. Compute hash H_orig = get_invoke_transaction_v3_hash(AllResources path)
   → poseidon([INVOKE, version, sender, tip_resource_hash_3_elements, ...])
   where tip_resource_hash_3_elements = poseidon([tip, L1_GAS_concat, L2_GAS_concat(0), L1_DATA_GAS_concat(0)])

4. Serialize to protobuf::ResourceBounds:
     { l1_gas: Some(1000/1), l2_gas: Some(0/0), l1_data_gas: Some(0/0) }

5. Deserialize via TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
     l1_data_gas.is_zero() && l2_gas.is_zero() == true
     → ValidResourceBounds::L1Gas({ max_amount: 1000, max_price_per_unit: 1 })

6. Attempt TryFrom<protobuf::InvokeV3> for RpcInvokeTransactionV3:
     match common.resource_bounds {
         ValidResourceBounds::AllResources(_) => ...,
         _ => return Err(DEPRECATED_RESOURCE_BOUNDS_ERROR),  // ← triggered
     }
   → Transaction rejected on all P2P peers.

7. If step 6 were bypassed and hash recomputed under L1Gas path:
   H_recomputed = poseidon([INVOKE, version, sender, tip_resource_hash_2_elements, ...])
   where tip_resource_hash_2_elements = poseidon([tip, L1_GAS_concat, L2_GAS_concat(0)])
   → H_recomputed ≠ H_orig → signature verification fails.
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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L169-192)
```rust
impl TryFrom<protobuf::DeclareV3WithClass> for RpcDeclareTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::DeclareV3WithClass) -> Result<Self, Self::Error> {
        let (common, class) = value.try_into()?;
        Ok(Self {
            resource_bounds: match common.resource_bounds {
                ValidResourceBounds::AllResources(resource_bounds) => resource_bounds,
                _ => {
                    return Err(DEPRECATED_RESOURCE_BOUNDS_ERROR);
                }
            },
            sender_address: common.sender_address,
            signature: common.signature,
            nonce: common.nonce,
            compiled_class_hash: common.compiled_class_hash,
            contract_class: class,
            tip: common.tip,
            paymaster_data: common.paymaster_data,
            account_deployment_data: common.account_deployment_data,
            nonce_data_availability_mode: common.nonce_data_availability_mode,
            fee_data_availability_mode: common.fee_data_availability_mode,
        })
    }
}
```

### Title
Protobuf `ValidResourceBounds` Deserialization Heuristic Silently Mutates `AllResources` to `L1Gas` Variant, Producing a Different Transaction Hash After P2P Round-Trip — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a **value-based heuristic** (checking whether `l2_gas` and `l1_data_gas` are zero) to decide which enum variant to reconstruct. Because `get_tip_resource_bounds_hash` includes `L1_DATA_GAS` in the hash preimage only for the `AllResources` variant, a transaction submitted with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` produces hash **H1** at the gateway, but after a protobuf round-trip the same transaction is reconstructed as `L1Gas { l1_gas: X }` and produces a **different** hash **H2**. This is a direct analog of the external M-19 bug: a count/type mismatch between the sender's view and the receiver's view of a structured field causes the receiver to bind the wrong hash to the transaction.

---

### Finding Description

**Step 1 – Gateway always uses `AllResources`.**

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds`. The gateway converts it to `InternalRpcInvokeTransactionV3`, whose `InvokeTransactionV3Trait` implementation always wraps the bounds in `ValidResourceBounds::AllResources`: [1](#0-0) 

The hash is then computed by `get_invoke_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash`.

**Step 2 – Hash preimage length depends on the variant.**

`get_tip_resource_bounds_hash` builds a `resource_felts` vector. For `AllResources` it appends a third element (`L1_DATA_GAS`); for `L1Gas` it does not: [2](#0-1) 

So `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` hashes as `poseidon(tip, L1_GAS, L2_GAS_zero, L1_DATA_GAS_zero)` — **three** resource elements — while `L1Gas(l1_gas=X)` hashes as `poseidon(tip, L1_GAS, L2_GAS_zero)` — **two** elements. These are distinct Felt values.

**Step 3 – Protobuf serialization loses the variant tag.**

The protobuf `ResourceBounds` message has no type discriminant. When `ValidResourceBounds::L1Gas` is serialized, `l2_gas` and `l1_data_gas` are written as zero: [3](#0-2) 

An `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` value is serialized identically.

**Step 4 – Deserialization uses a value-based heuristic that silently changes the variant.**

On the receiving side, the deserializer checks whether the decoded values are zero to pick the variant: [4](#0-3) 

Because both `l1_data_gas` and `l2_gas` are zero, the result is `ValidResourceBounds::L1Gas(l1_gas=X)` — the variant has changed from `AllResources` to `L1Gas` without any error.

**Step 5 – The gateway accepts the triggering transaction.**

The stateless validator only checks that `max_possible_fee > 0`. With `l1_gas` non-zero and `l2_gas = l1_data_gas = 0`, the check passes: [5](#0-4) 

Any unprivileged user can submit such a transaction.

---

### Impact Explanation

After the protobuf round-trip the transaction's hash changes from H1 (computed with three resource elements) to H2 (computed with two). Any component that recomputes the hash from the deserialized `InvokeTransactionV3` — including P2P block-sync hash verification, `validate_transaction_hash`, or RPC simulation/tracing — will produce H2 while the block header records H1. This constitutes:

- **Wrong hash bound to the transaction**: the hash no longer uniquely identifies the transaction across the P2P boundary, matching the "wrong hash / signature preimage" impact category.
- **Block sync rejection**: a peer that recomputes transaction hashes during block validation will reject a valid block, causing consensus or sync failure.
- **Authoritative-looking wrong RPC value**: `starknet_getTransactionByHash(H1)` returns fields that, when re-hashed by a client, yield H2 ≠ H1.

---

### Likelihood Explanation

The trigger requires only a standard V3 invoke transaction with `l2_gas = 0` and `l1_data_gas = 0` — a configuration that is valid today (pre-0.13.3 style bounds submitted through the new `AllResourceBounds` API). No privileged access, special contract, or malformed bytes are needed. The protobuf path is exercised on every P2P block propagation.

---

### Recommendation

Add an explicit type discriminant to the protobuf `ResourceBounds` message (e.g., a `bool is_all_resources` field or a `oneof` wrapper), and use it — not the zero-value heuristic — to reconstruct the correct `ValidResourceBounds` variant. Until the proto schema is updated, the deserializer should treat any message that carries all three fields (even if zero) as `AllResources`, matching the behavior of the JSON deserializer which uses key presence rather than value magnitude: [6](#0-5) 

---

### Proof of Concept

1. Submit an invoke V3 transaction via RPC with `resource_bounds = { l1_gas: { max_amount: 1, max_price_per_unit: 1 }, l2_gas: { max_amount: 0, max_price_per_unit: 0 }, l1_data_gas: { max_amount: 0, max_price_per_unit: 0 } }`.
2. The gateway accepts it (fee check passes because l1_gas > 0) and computes hash H1 using `AllResources` with three resource elements.
3. Serialize the resulting `InvokeTransactionV3` to protobuf via `From<ValidResourceBounds> for protobuf::ResourceBounds` — both `l2_gas` and `l1_data_gas` are written as zero.
4. Deserialize on a peer via `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` — the heuristic `l1_data_gas.is_zero() && l2_gas.is_zero()` fires, yielding `L1Gas`.
5. Recompute the hash on the peer — `get_tip_resource_bounds_hash` now builds a two-element preimage, producing H2 ≠ H1.
6. The peer's hash verification fails; the block containing this transaction is rejected.

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
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

**File:** crates/starknet_api/src/transaction/fields.rs (L575-606)
```rust
impl TryFrom<DeprecatedResourceBoundsMapping> for ValidResourceBounds {
    type Error = StarknetApiError;
    fn try_from(
        resource_bounds_mapping: DeprecatedResourceBoundsMapping,
    ) -> Result<Self, Self::Error> {
        if let (Some(l1_bounds), Some(l2_bounds)) = (
            resource_bounds_mapping.0.get(&Resource::L1Gas),
            resource_bounds_mapping.0.get(&Resource::L2Gas),
        ) {
            match resource_bounds_mapping.0.get(&Resource::L1DataGas) {
                Some(data_bounds) => Ok(Self::AllResources(AllResourceBounds {
                    l1_gas: *l1_bounds,
                    l1_data_gas: *data_bounds,
                    l2_gas: *l2_bounds,
                })),
                None => {
                    if l2_bounds.is_zero() {
                        Ok(Self::L1Gas(*l1_bounds))
                    } else {
                        Err(StarknetApiError::InvalidResourceMappingInitializer(format!(
                            "Missing data gas bounds but L2 gas bound is not zero: \
                             {resource_bounds_mapping:?}",
                        )))
                    }
                }
            }
        } else {
            Err(StarknetApiError::InvalidResourceMappingInitializer(format!(
                "{resource_bounds_mapping:?}",
            )))
        }
    }
```

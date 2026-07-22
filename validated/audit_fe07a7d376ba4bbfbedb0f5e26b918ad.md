### Title
Protobuf `ValidResourceBounds` Deserialization Uses Zero-Value Heuristic Instead of Type Discriminant, Causing Transaction Hash Divergence Across Nodes - (File: crates/apollo_protobuf/src/converters/transaction.rs)

### Summary

The protobuf deserialization of `ResourceBounds` into `ValidResourceBounds` uses a value-based heuristic (`l1_data_gas.is_zero() && l2_gas.is_zero()`) to select between the `L1Gas` and `AllResources` variants. This is structurally identical to the reported `_getIndex` returning `0` when a token is not found: a missing or zero discriminant silently selects the wrong variant. Because `get_tip_resource_bounds_hash` produces a different hash preimage for `L1Gas` vs `AllResources` (the latter includes `L1_DATA_GAS` in the Poseidon chain), a transaction submitted via JSON-RPC as `AllResources` with zero L2 and data gas will have its hash computed differently by receiving nodes that deserialize it over protobuf, causing a canonical hash divergence across the network.

### Finding Description

In `crates/apollo_protobuf/src/converters/transaction.rs`, the `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` implementation silently defaults the absent `l1_data_gas` field to zero and then uses a value check to select the variant:

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

The JSON deserialization path (`TryFrom<DeprecatedResourceBoundsMapping>`) correctly distinguishes the two variants by the *presence* of the `L1DataGas` key, not its value:

```rust
match resource_bounds_mapping.0.get(&Resource::L1DataGas) {
    Some(data_bounds) => Ok(Self::AllResources(...)),
    None => { if l2_bounds.is_zero() { Ok(Self::L1Gas(...)) } else { Err(...) } }
}
``` [2](#0-1) 

A user can submit a JSON V3 transaction with `L1_DATA_GAS` explicitly present but zero-valued alongside zero `L2_GAS`. The gateway deserializes this as `AllResources` and computes the transaction hash using `get_tip_resource_bounds_hash`, which for `AllResources` appends `concat(L1_DATA_GAS, zero_bounds)` to the Poseidon chain:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
``` [3](#0-2) 

When this transaction is propagated to peers via the mempool protobuf path (`MempoolTransaction` with `transaction_hash: None`), the receiving node must recompute the hash from the deserialized transaction. The protobuf deserialization sees `l1_data_gas = 0` and `l2_gas = 0` and selects `ValidResourceBounds::L1Gas`. The hash is then computed *without* the `L1_DATA_GAS` term, producing a different value than the originating node computed. [4](#0-3) 

### Impact Explanation

The transaction hash computed by the originating node (using `AllResources` preimage) diverges from the hash computed by receiving nodes (using `L1Gas` preimage). Since `MempoolTransaction` transmits `transaction_hash: None`, receiving nodes recompute the hash and store the transaction under the wrong key. When the originating node proposes a block containing the transaction under hash H1, peers cannot locate it in their mempool under H1 (they stored it as H2), causing consensus failures or wrong committed state. This matches: **High. Transaction conversion or signature/hash logic binds the wrong hash, type, or executable payload.**

### Likelihood Explanation

A user must submit a V3 transaction with `AllResources` where both `L2_GAS` and `L1_DATA_GAS` are explicitly present in the JSON but zero-valued. The JSON deserialization path accepts this (the `L1DataGas` key is present, so `AllResources` is selected). This is an unusual but valid configuration. No privileged access is required.

### Recommendation

Replace the value-based heuristic with an explicit type discriminant in the protobuf schema. Add a boolean or enum field (e.g., `resource_bounds_type`) to `protobuf::ResourceBounds` to distinguish `L1Gas` from `AllResources`, analogous to the recommended fix in the original report (use a mapping/flag instead of relying on array values). Until the protobuf schema is updated, the deserialization should treat any message where `l1_data_gas` is explicitly `Some(...)` (even if zero) as `AllResources`, and only fall back to `L1Gas` when `l1_data_gas` is `None`.

### Proof of Concept

1. Construct a JSON V3 invoke transaction with:
   ```json
   "resource_bounds": {
     "L1_GAS": {"max_amount": "0x1", "max_price_per_unit": "0x1"},
     "L2_GAS": {"max_amount": "0x0", "max_price_per_unit": "0x0"},
     "L1_DATA_GAS": {"max_amount": "0x0", "max_price_per_unit": "0x0"}
   }
   ```
2. Submit to the gateway. The gateway deserializes via `TryFrom<DeprecatedResourceBoundsMapping>`: `L1DataGas` key is present → `AllResources`. Hash H1 is computed including `L1_DATA_GAS` in the Poseidon chain.
3. The gateway propagates the transaction to a peer via `MempoolTransaction` protobuf with `transaction_hash: None`.
4. The peer deserializes via `TryFrom<protobuf::ResourceBounds>`: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `L1Gas`. Hash H2 is computed *without* `L1_DATA_GAS`.
5. H1 ≠ H2. The peer stores the transaction under H2. When the originating node proposes a block with H1, the peer cannot match it, causing a consensus divergence. [5](#0-4) [6](#0-5)

### Citations

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

**File:** crates/starknet_api/src/transaction/fields.rs (L580-606)
```rust
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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L56-74)
```rust
                    // TODO(alonl): Consider removing transaction hash from protobuf
                    transaction_hash: None,
                }
            }
            RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(txn)) => {
                protobuf::MempoolTransaction {
                    txn: Some(protobuf::mempool_transaction::Txn::DeployAccountV3(txn.into())),
                    // TODO(alonl): Consider removing transaction hash from protobuf
                    transaction_hash: None,
                }
            }
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(txn)) => {
                protobuf::MempoolTransaction {
                    txn: Some(protobuf::mempool_transaction::Txn::InvokeV3(txn.into())),
                    // TODO(alonl): Consider removing transaction hash from protobuf
                    transaction_hash: None,
                }
            }
        }
```

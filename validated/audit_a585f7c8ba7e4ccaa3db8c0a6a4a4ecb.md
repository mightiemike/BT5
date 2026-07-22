### Title
`ValidResourceBounds::AllResources` silently collapses to `L1Gas` in protobuf round-trip, corrupting the transaction hash preimage — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a zero-value heuristic to decide between the `L1Gas` and `AllResources` variants. This heuristic is inconsistent with the JSON/serde deserializer, which uses key-presence. Because `get_tip_resource_bounds_hash` hashes a **different number of resource elements** for each variant, any `AllResources` transaction whose `l2_gas` and `l1_data_gas` are both zero is silently re-typed to `L1Gas` during protobuf deserialization, producing a different hash than the one the proposing node computed and stored.

### Finding Description

**Hash preimage is variant-dependent.**
`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` builds the resource-bounds hash by conditionally appending the `L1_DATA_GAS` element only for `AllResources`:

```rust
// L1 and L2 gas bounds always exist.
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];
// For new V3 txs, need to also hash the data gas bounds.
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 elements
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 elements
    }
});
```

So `Poseidon(tip, L1_GAS_felt, L2_GAS_felt)` ≠ `Poseidon(tip, L1_GAS_felt, L2_GAS_felt, L1_DATA_GAS_felt)` even when `L2_GAS_felt` and `L1_DATA_GAS_felt` are both zero.

**Protobuf deserializer uses a zero-value heuristic.**
`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` decides the variant purely by checking whether both decoded fields are zero:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← collapses AllResources to L1Gas
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

**JSON/serde deserializer uses key-presence.**
`TryFrom<DeprecatedResourceBoundsMapping> for ValidResourceBounds` in `crates/starknet_api/src/transaction/fields.rs` preserves `AllResources` whenever the `L1DataGas` key is present in the map, regardless of its value:

```rust
match resource_bounds_mapping.0.get(&Resource::L1DataGas) {
    Some(data_bounds) => Ok(Self::AllResources(AllResourceBounds { ... })),  // key present → AllResources
    None => {
        if l2_bounds.is_zero() { Ok(Self::L1Gas(*l1_bounds)) } else { Err(...) }
    }
}
```

**Serializer always emits `l1_data_gas`.**
The `From<ValidResourceBounds> for protobuf::ResourceBounds` serializer always sets `l1_data_gas`, even for `L1Gas`:

```rust
ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
    l1_gas: Some(l1_gas.into()),
    l2_gas: Some(value.get_l2_bounds().into()),
    l1_data_gas: Some(ResourceBounds::default().into()),  // zero, always present
},
```

This means the wire format for `L1Gas` and for `AllResources{l2_gas=0, l1_data_gas=0}` is **byte-for-byte identical**. The deserializer cannot distinguish them and always reconstructs `L1Gas`, silently discarding the `AllResources` variant and producing a 2-element hash preimage instead of the correct 3-element one.

**Concrete divergence path:**

| Step | Node / Path | Variant | Hash elements |
|------|-------------|---------|---------------|
| Gateway receives tx | JSON → `AllResources{l1_gas=X, l2_gas=0, l1_data_gas=0}` | `AllResources` | 3 |
| Hash computed & stored | `get_invoke_transaction_v3_hash` | `AllResources` | 3 |
| P2P sync to peer | protobuf round-trip | **`L1Gas`** | **2** |
| Peer recomputes hash | `get_invoke_transaction_v3_hash` | `L1Gas` | 2 |
| Hash mismatch | stored ≠ recomputed | — | — |

### Impact Explanation

A syncing node that recomputes the transaction hash from the protobuf-deserialized transaction (e.g., during `validate_transaction_hash` in the storage layer, or during account-validation re-execution) will obtain a hash that differs from the one the proposing node committed to the block. This falls under:

- **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload**: the protobuf conversion silently changes the hash domain of a valid V3 transaction, so any component that re-derives the hash from the deserialized object will produce the wrong value.
- **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value**: an RPC node that re-hashes a synced transaction will return the wrong `transaction_hash`.

### Likelihood Explanation

The gateway's `validate_resource_bounds` check rejects transactions where `l2_gas.max_price_per_unit < min_gas_price`. If `min_gas_price = 0` (or `validate_resource_bounds = false`), a transaction with `AllResources{l2_gas=0, l1_data_gas=0}` passes all gateway checks and is accepted. The `max_possible_fee` guard only requires at least one non-zero resource, so a non-zero `l1_gas` bound satisfies it. The collision is therefore reachable by any user on a node with `min_gas_price = 0`.

### Recommendation

1. **Add a variant discriminator to the protobuf message** (e.g., a boolean `is_all_resources` field or an enum) so the deserializer can reconstruct the correct variant without relying on zero-value heuristics.
2. Until the protobuf schema is updated, the deserializer should treat any message that was serialized from `AllResources` as `AllResources`, even when all non-L1-gas fields are zero. One approach: always emit a sentinel non-zero value in a reserved field for `AllResources`, and check for its presence on deserialization.
3. Add a round-trip test asserting that `AllResources{l1_gas=X, l2_gas=0, l1_data_gas=0}` survives a protobuf serialize/deserialize cycle with the same variant and the same transaction hash.

### Proof of Concept

```
// Proposing node (gateway path, JSON/serde):
let bounds = ValidResourceBounds::AllResources(AllResourceBounds {
    l1_gas:      ResourceBounds { max_amount: GasAmount(1000), max_price_per_unit: GasPrice(1) },
    l2_gas:      ResourceBounds::default(),   // zero
    l1_data_gas: ResourceBounds::default(),   // zero
});
// hash_proposer = Poseidon(tip, L1_GAS_felt, L2_GAS_felt_zero, L1_DATA_GAS_felt_zero)
// → 3-element preimage

// Syncing node (P2P path, protobuf):
let proto = protobuf::ResourceBounds::from(bounds);
// proto.l1_data_gas = Some(ResourceLimits { max_amount: 0, max_price_per_unit: 0 })
// proto.l2_gas      = Some(ResourceLimits { max_amount: 0, max_price_per_unit: 0 })

let reconstructed = ValidResourceBounds::try_from(proto).unwrap();
// l1_data_gas.is_zero() && l2_gas.is_zero() → ValidResourceBounds::L1Gas(l1_gas)
// hash_syncer = Poseidon(tip, L1_GAS_felt, L2_GAS_felt_zero)
// → 2-element preimage

assert_ne!(hash_proposer, hash_syncer);  // FAILS: hashes diverge
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** crates/starknet_api/src/transaction_hash.rs (L370-404)
```rust
pub(crate) fn get_invoke_transaction_v3_hash<T: InvokeTransactionV3Trait>(
    transaction: &T,
    chain_id: &ChainId,
    transaction_version: &TransactionVersion,
) -> Result<TransactionHash, StarknetApiError> {
    let tip_resource_bounds_hash =
        get_tip_resource_bounds_hash(&transaction.resource_bounds(), transaction.tip())?;
    let paymaster_data_hash =
        HashChain::new().chain_iter(transaction.paymaster_data().0.iter()).get_poseidon_hash();
    let data_availability_mode = concat_data_availability_mode(
        transaction.nonce_data_availability_mode(),
        transaction.fee_data_availability_mode(),
    );
    let account_deployment_data_hash = HashChain::new()
        .chain_iter(transaction.account_deployment_data().0.iter())
        .get_poseidon_hash();
    let calldata_hash =
        HashChain::new().chain_iter(transaction.calldata().0.iter()).get_poseidon_hash();
    let mut hash_chain = HashChain::new()
        .chain(&INVOKE)
        .chain(&transaction_version.0)
        .chain(transaction.sender_address().0.key())
        .chain(&tip_resource_bounds_hash)
        .chain(&paymaster_data_hash)
        .chain(&Felt::try_from(chain_id)?)
        .chain(&transaction.nonce().0)
        .chain(&data_availability_mode)
        .chain(&account_deployment_data_hash)
        .chain(&calldata_hash);
    if !transaction.proof_facts().0.is_empty() {
        let proof_facts_hash =
            HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
        hash_chain = hash_chain.chain(&proof_facts_hash);
    }
    Ok(TransactionHash(hash_chain.get_poseidon_hash()))
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

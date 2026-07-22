### Title
`ValidResourceBounds` Variant Derived from Runtime Values in Protobuf Conversion Produces Wrong Transaction Hash Preimage — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion in the block-sync path derives the `ValidResourceBounds` variant (`L1Gas` vs `AllResources`) from the **runtime values** of `l2_gas` and `l1_data_gas` rather than from an explicit type tag. When a V3 transaction carrying `AllResources` bounds with both `l2_gas` and `l1_data_gas` set to zero is serialized to protobuf and deserialized, it is silently reconstructed as `ValidResourceBounds::L1Gas`. Because `get_tip_resource_bounds_hash` produces a **structurally different hash preimage** for `L1Gas` (2 resource felts) versus `AllResources` with zero l2/l1_data bounds (3 resource felts), `validate_transaction_hash` will compute a hash that diverges from the hash the user signed and the sequencer stored, causing the block to be rejected by syncing nodes.

---

### Finding Description

**Root cause — value-derived variant in protobuf deserialization** [1](#0-0) 

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant inferred from values
} else {
    ValidResourceBounds::AllResources(...)
})
```

There is no explicit discriminant in the protobuf wire format. The variant is **derived** from whether the deserialized `l2_gas` and `l1_data_gas` fields are zero — exactly the same pattern as the GOAT `aEthValue = _geth.balanceOf(address(this))` derivation.

**Hash preimage divergence**

`get_tip_resource_bounds_hash` branches on the variant to decide how many resource felts to include in the Poseidon hash: [2](#0-1) 

- `ValidResourceBounds::L1Gas(_)` → hashes `[tip, L1_GAS_packed, L2_GAS_packed_zero]` **(2 felts)**
- `ValidResourceBounds::AllResources(...)` → hashes `[tip, L1_GAS_packed, L2_GAS_packed_zero, L1_DATA_GAS_packed_zero]` **(3 felts)**

Poseidon is sensitive to the number of elements, so these two produce **different field elements** even when the economic content is identical (same l1_gas bounds, zero l2 and l1_data bounds).

**Serialization path that preserves `AllResources`**

When the gateway accepts a V3 transaction, `InternalRpcInvokeTransactionV3::resource_bounds()` unconditionally returns `ValidResourceBounds::AllResources(self.resource_bounds)`: [3](#0-2) 

The hash is computed once with the 3-felt preimage and stored in `InternalRpcTransaction.tx_hash`. [4](#0-3) 

**Serialization path that loses the variant**

When the same transaction is serialized to protobuf for block sync, `From<ValidResourceB

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

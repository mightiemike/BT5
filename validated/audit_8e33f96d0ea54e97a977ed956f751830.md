### Title
Protobuf `ValidResourceBounds` round-trip silently coerces `AllResources{l2_gas=0, l1_data_gas=0}` to `L1Gas`, diverging the transaction hash preimage - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` silently defaults the absent `l1_data_gas` field and then applies a zero-check heuristic to decide between the `L1Gas` and `AllResources` enum variants. A transaction legitimately submitted with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` serializes to protobuf with all three fields present as zero, but deserializes back as `L1Gas(X)`. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` felt in the Poseidon preimage only for the `AllResources` variant, the two variants produce structurally different hash inputs for identical economic content, breaking the canonicalization invariant that a transaction's hash must be stable across serialization boundaries.

### Finding Description

**Root cause — `crates/apollo_protobuf/src/converters/transaction.rs` lines 417–436:**

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let Some(l1_gas) = value.l1_gas else { return Err(missing("ResourceBounds::l1_gas")); };
        let Some(l2_gas) = value.l2_gas else { return Err(missing("ResourceBounds::l2_gas")); };
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // ← silent default
        let l1_gas: ResourceBounds = l1_gas.try_into()?;
        let l2_gas: ResourceBounds = l2_gas.try_into()?;
        let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)                     // ← variant changes
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l
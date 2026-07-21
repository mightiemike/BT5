### Title
`ValidResourceBounds::AllResources` with zero L2/L1-data-gas silently collapses to `ValidResourceBounds::L1Gas` in protobuf deserialization, producing a divergent transaction hash — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf converter `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` silently maps any `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` to `L1Gas(X)`. Because `get_tip_resource_bounds_hash` hashes a different number of resource-bound elements for the two variants, the transaction hash computed at the gateway (using `AllResources`) diverges from the hash recomputed after protobuf round-trip (using `L1Gas`). Any transaction submitted via RPC with zero L2 and L1-data-gas bounds will therefore fail hash validation on a syncing or peer node.

### Finding Description

**Step 1 – Hash domain split in `get_tip_resource_bounds_hash`**

`crates/starknet_api/src/transaction_hash.rs` lines 188–211 build the fee-fields hash by appending resource-bound felts conditionally on the variant:

```
L1Gas(X)          → poseidon([tip, L1_gas_packed, L2_gas_packed(0)])          // 3 elements
AllResources{X,0,0} → poseidon([tip, L1_gas_packed, L2_gas_packed(0), L1_data_gas_packed(0)])  // 4 elements
```

`L1_data_gas_packed(0)` is **not** the zero felt — `get_concat_resource` encodes the 7-byte ASCII resource name `"L1_DATA"` into the upper bits, so the extra element is always non-zero. The two variants therefore produce irreconcilably different hashes even when all numeric bounds are identical. [1](#0-0) 

**Step 2 – Silent variant collapse in protobuf deserialization**

`crates/apollo_protobuf/src/converters/transaction.rs` lines 417–437 implement `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`:

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant changes here
} else {
    ValidResourceBounds::AllResources(...)
})
```

When `l1_data_gas` is absent (old 0.13.2 peers) or explicitly

### Citations

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

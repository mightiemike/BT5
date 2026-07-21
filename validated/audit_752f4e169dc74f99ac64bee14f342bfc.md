### Title
`ValidResourceBounds` Misclassification in Protobuf Deserialization Causes Transaction Hash Divergence - (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary
The protobuf converter for `ValidResourceBounds` uses a value-based heuristic — checking whether `l2_gas` and `l1_data_gas` are both zero — to decide whether to reconstruct a `L1Gas` or `AllResources` variant. An `AllResources` transaction whose L2 and L1_data_gas bounds are legitimately zero is silently misclassified as `L1Gas` after P2P deserialization. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` element only for `AllResources`, the hash computed after deserialization diverges from the hash computed at submission time, causing the transaction to be rejected by receiving nodes or, in paths that do not re-validate the hash, to be stored under a wrong hash.

### Finding Description

**Root cause — the heuristic classifier:**

In `crates/apollo_protobuf/src/converters/transaction.rs` at lines 417–436, the converter infers the `ValidResourceBounds` variant from the decoded values rather than from an explicit type tag:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

This is the direct analog of the `_isWrappedFCash` try-catch: instead of checking a canonical discriminant, it infers the type from the payload values. An `AllResources` transaction where both `l2_gas` and `l1_data_gas` happen to be zero (a user who sets only L1 gas bounds, which is valid) is silently misclassified as `L1Gas`.

**Hash divergence — the downstream consequence:**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` at lines 188–211 branches on the variant:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
```

- `L1Gas` path: `poseidon(tip, L1_GAS_packed, L2_GAS_packed=0)` — **2 resource elements**
- `AllResources` with zero L2/L1_data_gas: `poseidon(tip, L1_GAS_packed, L2_GAS_packed=0, L1_DATA_GAS_packed=0)` — **3 resource elements**

These produce different Poseidon outputs. `get_invoke_transaction_v3_hash` (lines 370–404) chains this hash as a single element, so the divergence propagates to the final `
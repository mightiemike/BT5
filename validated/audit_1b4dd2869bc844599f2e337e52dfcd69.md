### Title
`ValidResourceBounds::AllResources(l1_gas, 0, 0)` collapses to `L1Gas` after protobuf round-trip, producing a divergent transaction hash and rejecting valid transactions in P2P mempool sync — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a value-based heuristic to reconstruct the enum variant. When `l1_data_gas == 0 && l2_gas == 0`, it always returns `L1Gas`, even if the original transaction was submitted as `AllResources`. Because `get_tip_resource_bounds_hash` produces a **different hash** for `AllResources` (which includes `L1_DATA_GAS` in the preimage) versus `L1Gas` (which does not), a transaction whose hash was computed at submission time under `AllResources` will have a different hash if recomputed after the protobuf round-trip. Additionally, the P2P mempool converter for `RpcDeclareTransactionV3` explicitly rejects the `L1Gas` variant, so any declare transaction submitted with `AllResources(l1_gas, 0, 0)` is silently dropped by every peer that receives it via P2P.

### Finding Description

**Step 1 — Submission path always hashes as `AllResources`.**

`RpcDeclareTransactionV3` and `RpcInvokeTransactionV3` store `resource_bounds: AllResourceBounds` (not `ValidResourceBounds`). The `DeclareTransactionV3Trait` implementation for `InternalRpcDeclareTransactionV3` unconditionally wraps this in `ValidResourceBounds::AllResources`:

```rust
// crates/starknet_api/src/rpc_transaction.rs  lines 408-411
impl DeclareTransactionV3Trait for InternalRpcDeclareTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

`get_tip_resource_bounds_hash` then includes `L1_DATA_GAS` in the Poseidon preimage for `AllResources`:

```rust
// crates/starknet_api/src/transaction_hash.rs  lines 203-208
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
```

So a transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` gets a hash that includes a `L1_DATA_GAS` term (even though its value is zero).

**Step 2 — Protobuf serialization is lossy: both variants produce identical bytes.**

`ValidResourceBounds::L1Gas` and `ValidResourceBounds::AllResources(l1_gas, 0, 0)` serialize to the same protobuf `ResourceBounds` message (all three fields present, l2_gas and l1_data_gas both zero):

```rust
// crates/apollo_protobuf/src/converters/transaction.rs  lines 471-489
ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
    l1_gas: Some(l1_gas.into()),
    l2_gas: Some(value.get_l2_bounds().into()),          // → zero
    l1_data_gas: Some(ResourceBounds::default().into()), // → zero
},
ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
    protobuf::ResourceBounds {
        l1_gas: Some(l1_gas.into()),
        l2_gas: Some(l2_gas.into()),
        l1_data_gas: Some(l1_data_gas.into()),
    },
```

**Step 3 — Deserialization always reconstructs `L1Gas` when both auxiliary fields are zero.**

```rust
// crates/apollo_protobuf/src/converters/transaction.rs  lines 417
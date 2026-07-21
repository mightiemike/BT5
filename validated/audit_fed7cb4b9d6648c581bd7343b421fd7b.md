### Title
Protobuf `ValidResourceBounds` deserialization silently downcasts `AllResources` to `L1Gas` when l2_gas and l1_data_gas are zero, producing a wrong transaction hash - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion uses a value-based heuristic — checking whether the deserialized `l2_gas` and `l1_data_gas` fields are zero — to decide between `ValidResourceBounds::L1Gas` and `ValidResourceBounds::AllResources`. A valid gateway-accepted `AllResources` transaction whose `l2_gas` and `l1_data_gas` are both zero (e.g., only `l1_gas` is non-zero) is silently re-typed to `L1Gas` on the receiving side. Because `get_tip_resource_bounds_hash` hashes a different number of resource-bound fields depending on the variant, the transaction hash computed from the deserialized form diverges from the hash computed at submission
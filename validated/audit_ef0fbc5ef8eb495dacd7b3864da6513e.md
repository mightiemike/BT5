### Title
`ValidResourceBounds` Protobuf Round-Trip Silently Downgrades `AllResources` to `L1Gas`, Producing a Different Transaction Hash — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a value-based heuristic to reconstruct the variant. An `AllResources` V3 transaction whose `l2_gas` and `l1_data_gas` bounds are both zero serializes to the same protobuf bytes as an `L1Gas` transaction, but deserializes back as `L1Gas`. Because `get_tip_resource_bounds_hash` hashes
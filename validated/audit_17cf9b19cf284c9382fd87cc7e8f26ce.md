The question hinges on whether an unprivileged attacker can get `AllResources{l1_gas=X, l2_gas=0, l1_data_gas=0}` with an `AllResources`-variant hash into the system, then have the protobuf round-trip silently downgrade it to `L1Gas(X)`, producing a hash mismatch. Let me trace every entry path.

**The protobuf conversion is confirmed lossy:** [1](#0-0) 

When `l1_data_gas.is_zero() && l2_gas.is_zero()`, the result is `ValidResourceBounds::L1Gas(l1_gas)` regardless of the original variant. And the hash functions diverge:

<cite repo="patrichyt/sequ

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L431-435)
```rust
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
```

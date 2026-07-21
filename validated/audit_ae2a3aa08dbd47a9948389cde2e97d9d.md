### Title
`ValidResourceBounds` Variant Misclassification in Protobuf Conversion Produces Wrong Transaction Hash — (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf-to-`ValidResourceBounds` conversion infers the variant (`L1Gas` vs `AllResources`) solely by checking whether the numeric fields `l2_gas` and `l1_data_gas` are zero. Because `get_tip_resource_bounds_hash` produces structurally different hash inputs for the two variants (2 vs 3 resource-bound elements), an `AllResources` v3 transaction whose `l2_gas` and `l1_data_gas` happen to be zero will be silently reconstructed as `L1Gas` after a P2P protobuf round-trip, yielding a different hash than the one the signer committed to.

---

### Finding Description

**Protobuf conversion — partial discriminator check**

In `crates/apollo_protobuf/src/converters/transaction.rs`, `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` decides the variant purely by inspecting whether the numeric fields are zero:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

The protobuf wire format carries **no explicit discriminator bit** for the variant; the conversion infers it from value content alone. This is the direct analog of the single-byte discriminator: a partial (value-content) check is used instead of a canonical type tag.

**Hash function — structurally different inputs per variant**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` produces different hash inputs depending on the variant:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 elements total
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 elements total
    }
});
``` [2](#0-1) 

- `L1Gas` → `poseidon(tip, L1_GAS_packed, L2_GAS_packed)` — **2 resource elements**
- `AllResources` → `poseidon(tip, L1_GAS_packed, L2_GAS_packed, L1_DATA_GAS_packed)` — **3 resource elements**

Even when `l2_gas = {0, 0}` and `l1_data_gas = {0, 0}`, the `AllResources` hash includes the zero-valued `L1_DATA_GAS_packed` element, making it structurally distinct from the `L1Gas` hash.

**The divergence**

A transaction signed as `AllResources` with `l2_gas = {0, 0}` and `l1_data_gas = {0, 0}` commits to hash **H** (3-element poseidon). After protobuf round-trip the receiving node reconstructs `L1Gas` and recomputes hash **H′** (2-element poseidon). **H ≠ H′**. [3](#0-2) 

The `ValidResourceBounds` enum and its two variants are defined here: [4](#0-3) 

---

### Impact Explanation

Any node that re-derives the transaction hash after protobuf deserialization (e.g., during P2P sync admission or mempool re-validation) will compute **H′ ≠ H**. The transaction is rejected by peers even though it is cryptographically valid and was accepted by the gateway. This matches:

- **High: Mempool/gateway/RPC admission rejects valid transactions before sequencing.**
- **High: Transaction conversion or signature/hash logic binds the wrong hash/type.**

Additionally, if the hash is not re-verified, the transaction is stored with the wrong `ValidResourceBounds` variant. `get_gas_vector_computation_mode()` then returns `NoL2Gas` instead of `All`: [5](#0-4) 

This silently changes fee and gas accounting for the accepted transaction.

---

### Likelihood Explanation

Any user can craft a valid `AllResources` v3 transaction with zero `l2_gas` and `l1_data_gas` bounds. The gateway accepts it (the hash is correct at submission time). The mismatch surfaces only when the transaction is propagated via P2P and a peer re-derives the hash from the deserialized protobuf. No privileged access is required; the trigger is an ordinary transaction submission.

---

### Recommendation

Add an explicit discriminator field to the protobuf `ResourceBounds` message (e.g., a boolean `is_all_resources`) so the variant can be faithfully round-tripped without relying on value-content heuristics. Alternatively, always reconstruct as `AllResources` for v3 transactions and let the hash function handle the canonical form, since `L1Gas` is a pre-0.13.3 legacy variant that should never appear in new v3 transactions.

---

### Proof of Concept

1. Construct an `AllResources` v3 invoke transaction with `l2_gas = ResourceBounds { max_amount: 0, max_price_per_unit: 0 }` and `l1_data_gas = ResourceBounds { max_amount: 0, max_price_per_unit: 0 }`.
2. Submit to the gateway. The gateway computes:
   `H = poseidon(INVOKE, version, sender, poseidon(tip, L1_GAS_packed, L2_GAS_packed_zero, L1_DATA_GAS_packed_zero), ...)` and accepts the transaction.
3. The transaction is propagated via P2P as a protobuf message with `l1_gas`, `l2_gas={0,0}`, `l1_data_gas={0,0}`.
4. The receiving node's `TryFrom<protobuf::ResourceBounds>` fires the `l1_data_gas.is_zero() && l2_gas.is_zero()` branch and reconstructs `ValidResourceBounds::L1Gas(l1_gas)`.
5. The receiving node recomputes:
   `H′ = poseidon(INVOKE, version, sender, poseidon(tip, L1_GAS_packed, L2_GAS_packed_zero), ...)` — only 2 resource elements, no `L1_DATA_GAS`.
6. **H ≠ H′**. The transaction is rejected as having an invalid hash, despite being a legitimately signed transaction.

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L431-436)
```rust
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
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

**File:** crates/starknet_api/src/transaction/fields.rs (L363-367)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}
```

**File:** crates/starknet_api/src/transaction/fields.rs (L416-421)
```rust
    pub fn get_gas_vector_computation_mode(&self) -> GasVectorComputationMode {
        match self {
            Self::AllResources(_) => GasVectorComputationMode::All,
            Self::L1Gas(_) => GasVectorComputationMode::NoL2Gas,
        }
    }
```

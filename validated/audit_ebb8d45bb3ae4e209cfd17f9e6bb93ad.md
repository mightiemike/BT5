### Title
`ValidResourceBounds::AllResources` silently collapses to `ValidResourceBounds::L1Gas` in protobuf round-trip, producing a divergent transaction hash - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ResourceBounds` silently converts a `ValidResourceBounds::AllResources` value (with zero `l2_gas` and zero `l1_data_gas`) into `ValidResourceBounds::L1Gas`. Because `get_tip_resource_bounds_hash` hashes a **different number of resource-bound elements** depending on the variant, the transaction hash computed before the protobuf round-trip (H1, 4-element poseidon input) differs from the hash computed after it (H2, 3-element poseidon input). A transaction accepted by the gateway with hash H1 is therefore bound to the wrong hash after P2P propagation, causing valid transactions to be rejected or executed under the wrong resource-bound type.

---

### Finding Description

**Root cause — protobuf `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`** [1](#0-0) 

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // ← silently defaults to zero
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)                      // ← variant collapses
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

When a V3 transaction carries `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` (a legal post-0.13.3 transaction where the user explicitly set both to zero), the protobuf field `l1_data_gas` is serialized as zero. On deserialization the `unwrap_or_default()` + zero-check silently promotes the result to `ValidResourceBounds::L1Gas(X)` — a structurally different variant.

**Hash divergence — `get_tip_resource_bounds_hash`** [2](#0-1) 

```rust
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,   // L2Gas(0) always present
];
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],             // ← L1DataGas OMITTED
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // ← L1DataGas INCLUDED
    }
});
Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
```

- `AllResources { l2_gas:0, l1_data_gas:0 }` → poseidon over **[tip, L1Gas, L2Gas(0), L1DataGas(0)]** → hash **H1**
- `L1Gas` (after protobuf collapse) → poseidon over **[tip, L1Gas, L2Gas(0)]** → hash **H2 ≠ H1**

The two variants produce structurally different hash preimages even when all numeric values are identical.

**Propagation path**

The mempool P2P protocol (`MempoolTransaction` gossipsub) carries the transaction hash alongside the transaction body. [3](#0-2) 

A receiving node deserializes the protobuf body → gets `L1Gas` → recomputes hash H2 → compares with the transmitted H1 → mismatch → rejects the transaction. The same divergence applies to the P2P state-sync path (`TransactionsResponse`), where the stored `FullTransaction` carries H1 but any node that re-derives the hash from the deserialized body obtains H2.

The `ValidResourceBounds` enum documents the invariant explicitly: [4](#0-3) 

`L1Gas` is "Pre 0.13.3" — a transaction submitted under the post-0.13.3 `AllResources` schema must never be silently re-classified as a pre-0.13.3 `L1Gas` transaction.

---

### Impact Explanation

A V3 transaction with `AllResources { l2_gas: 0, l1_data_gas: 0 }` is accepted by the gateway with hash H1. After protobuf round-trip (mempool gossipsub or P2P sync), every other node reconstructs the transaction as `L1Gas` and derives hash H2 ≠ H1. This causes:

1. **Valid transactions rejected before sequencing** — nodes that verify the hash after deserialization drop the transaction, matching the "High. Mempool/gateway/RPC admission rejects valid transactions before sequencing" impact.
2. **Wrong executable payload** — if the hash is trusted as-is but the resource-bound variant is used for fee/gas accounting, the transaction executes under `L1Gas` semantics (2-resource fee model) instead of `AllResources` semantics (3-resource fee model), producing wrong gas accounting and wrong fee deduction.
3. **Hash binding to wrong type** — matches "High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."

---

### Likelihood Explanation

The trigger requires a user to submit a V3 transaction with `AllResources` where both `l2_gas` and `l1_data_gas` are explicitly zero. This is an edge case but is entirely user-controlled and requires no privilege. The TODO comment in the protobuf converter (`// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2`) confirms the collapse is a known temporary workaround that has not been gated off, leaving the invariant broken for any post-0.13.3 transaction that happens to carry zero values for the new resource fields.

---

### Recommendation

In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, replace the silent `unwrap_or_default()` + zero-collapse with an explicit version-aware check. Once support for 0.13.2 is dropped (as the TODO intends), require `l1_data_gas` to be present and always produce `AllResources`:

```rust
// After 0.13.2 support is removed:
let l1_data_gas = value.l1_data_gas
    .ok_or_else(|| missing("ResourceBounds::l1_data_gas"))?;
let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))
```

Until then, preserve the variant by checking whether the *sender* indicated `AllResources` (e.g., via a version field or by requiring `l1_data_gas` to be present as a non-`None` protobuf field) rather than inferring the variant from numeric zero values.

---

### Proof of Concept

1. Submit a V3 invoke transaction to the gateway with:
   ```json
   "resource_bounds": {
     "l1_gas":      { "max_amount": "0x100", "max_price_per_unit": "0x1" },
     "l2_gas":      { "max_amount": "0x0",   "max_price_per_unit": "0x0" },
     "l1_data_gas": { "max_amount": "0x0",   "max_price_per_unit": "0x0" }
   }
   ```
   The JSON deserializer sees `L1DataGas` key present → `AllResources`. Gateway computes H1 = poseidon([tip, L1Gas, L2Gas(0), L1DataGas(0), …]).

2. The gateway propagates the transaction via mempool gossipsub as a `MempoolTransaction` protobuf. The `ResourceBounds` protobuf message carries `l1_data_gas = { max_amount: 0, max_price_per_unit: 0 }`.

3. A receiving node deserializes the protobuf. `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas`. The node computes H2 = poseidon([tip, L1Gas, L2Gas(0), …]) — **L1DataGas(0) is absent from the preimage**.

4. H1 ≠ H2. The receiving node rejects the transaction as having an invalid hash, or stores it under the wrong resource-bound type, causing wrong fee accounting when it is eventually executed.

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

**File:** crates/apollo_protobuf/src/proto/p2p/proto/mempool/transaction.proto (L12-19)
```text
message MempoolTransaction {
    oneof txn {
        DeclareV3WithClass declare_v3 = 1;
        DeployAccountV3 deploy_account_v3 = 2;
        InvokeV3WithProof invoke_v3 = 3;
    }
    Hash transaction_hash = 4;
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

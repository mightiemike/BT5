Now I have all the information needed. Let me analyze the exact code paths.

### Title
Protobuf `TryFrom<ResourceBounds>` Value-Based Zero-Check Misclassifies `AllResources` as `L1Gas`, Producing Divergent `get_tip_resource_bounds_hash` Felt — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

A confirmed serialization-boundary divergence exists between the JSON/RPC gateway path and the p2p protobuf sync path for `ValidResourceBounds`. An `AllResources{l1_gas=X, l2_gas=zero, l1_data_gas=zero}` transaction is classified as `AllResources` by the gateway (key-presence rule) but as `L1Gas` by the protobuf deserializer (value-zero rule). Because `get_tip_resource_bounds_hash` produces a structurally different Poseidon preimage for each variant, the two components compute different transaction hash `Felt` values for the same wire transaction.

---

### Finding Description

**Path 1 — JSON/RPC gateway (`TryFrom<DeprecatedResourceBoundsMapping>`):** [1](#0-0) 

Classification is **key-presence based**: if the `L1DataGas` key exists in the map, the result is `AllResources`, regardless of whether the value is zero.

**Path 2 — p2p protobuf sync (`TryFrom<protobuf::ResourceBounds>`):** [2](#0-1) 

Classification is **value-zero based**: if `l1_data_gas.is_zero() && l2_gas.is_zero()`, the result is `L1Gas`, even when `l1_data_gas` was explicitly present on the wire.

The serializer (`From<ValidResourceBounds> for protobuf::ResourceBounds`) always emits `l1_data_gas` for `AllResources`: [3](#0-2) 

So the round-trip `AllResources{l1_gas=X, l2_gas=zero, l1_data_gas=zero}` → protobuf bytes → `L1Gas{l1_gas=X}` is mechanically confirmed.

**Hash divergence in `get_tip_resource_bounds_hash`:** [4](#0-3) 

- `L1Gas` variant → Poseidon preimage: `[tip, L1_GAS_concat, L2_GAS_concat]` (2 resource felts)
- `AllResources` variant → Poseidon preimage: `[tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat]` (3 resource felts)

These produce **different Felt outputs** even when the underlying numeric gas values are identical. This hash feeds directly into `get_invoke_transaction_v3_hash`: [5](#0-4) 

---

### Impact Explanation

The gateway always classifies RPC V3 transactions as `AllResources` (the `RpcInvokeTransactionV3` type carries `AllResourceBounds` unconditionally). It computes and stores transaction hash **H1** using the 3-resource Poseidon preimage. When the same transaction is propagated to a p2p-synced node via protobuf, the deserializer reclassifies it as `L1Gas` and any hash recomputation produces **H2 ≠ H1**. Beyond the hash mismatch, the stored `ValidResourceBounds` variant on the synced node is `L1Gas`, which changes `get_gas_vector_computation_mode()` to `NoL2Gas`: [6](#0-5) 

This affects fee computation, gas accounting, and execution resource tracking for the accepted transaction.

---

### Likelihood Explanation

The input is trivially constructable: any V3 transaction with non-zero `l1_gas` and zero `l2_gas`/`l1_data_gas` triggers the divergence. No special privileges are required — any unprivileged user submitting an `invoke_v3` transaction via the public RPC endpoint with these bounds will produce the split. The TODO comment at line 426 of the protobuf converter explicitly acknowledges the ambiguity: [7](#0-6) 

---

### Recommendation

Replace the value-zero heuristic in `TryFrom<protobuf::ResourceBounds>` with an explicit discriminator field in the protobuf schema (e.g., a `bounds_type` enum or a dedicated `oneof`), so that `AllResources` vs `L1Gas` is encoded structurally rather than inferred from whether numeric fields happen to be zero. Until the schema is updated, the protobuf deserializer should treat any message that was serialized from an `AllResources` variant (i.e., where `l1_data_gas` is explicitly present, even if zero) as `AllResources`, matching the key-presence rule used by the JSON path.

---

### Proof of Concept

```rust
// Demonstrates the round-trip misclassification and hash divergence.
use starknet_api::transaction::fields::{
    AllResourceBounds, ResourceBounds, ValidResourceBounds,
};
use starknet_api::block::GasPrice;
use starknet_api::execution_resources::GasAmount;
use starknet_api::transaction::fields::Tip;
use starknet_api::transaction_hash::get_tip_resource_bounds_hash;
use apollo_protobuf::protobuf;

let all_resources = ValidResourceBounds::AllResources(AllResourceBounds {
    l1_gas: ResourceBounds {
        max_amount: GasAmount(1000),
        max_price_per_unit: GasPrice(1),
    },
    l2_gas: ResourceBounds::default(),   // zero
    l1_data_gas: ResourceBounds::default(), // zero
});

// Serialize to protobuf and back.
let proto: protobuf::ResourceBounds = all_resources.into();
let round_tripped = ValidResourceBounds::try_from(proto).unwrap();

// Misclassification: AllResources → L1Gas
assert!(matches!(round_tripped, ValidResourceBounds::L1Gas(_)));

// Hash divergence
let tip = Tip(0);
let hash_all = get_tip_resource_bounds_hash(&all_resources, &tip).unwrap();
let hash_l1  = get_tip_resource_bounds_hash(&round_tripped, &tip).unwrap();
assert_ne!(hash_all, hash_l1); // Different Poseidon preimage lengths → different Felt
```

### Citations

**File:** crates/starknet_api/src/transaction/fields.rs (L416-421)
```rust
    pub fn get_gas_vector_computation_mode(&self) -> GasVectorComputationMode {
        match self {
            Self::AllResources(_) => GasVectorComputationMode::All,
            Self::L1Gas(_) => GasVectorComputationMode::NoL2Gas,
        }
    }
```

**File:** crates/starknet_api/src/transaction/fields.rs (L584-589)
```rust
            match resource_bounds_mapping.0.get(&Resource::L1DataGas) {
                Some(data_bounds) => Ok(Self::AllResources(AllResourceBounds {
                    l1_gas: *l1_bounds,
                    l1_data_gas: *data_bounds,
                    l2_gas: *l2_bounds,
                })),
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L426-427)
```rust
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L431-435)
```rust
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L479-487)
```rust
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => protobuf::ResourceBounds {
                l1_gas: Some(l1_gas.into()),
                l2_gas: Some(l2_gas.into()),
                l1_data_gas: Some(l1_data_gas.into()),
            },
```

**File:** crates/starknet_api/src/transaction_hash.rs (L197-210)
```rust
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
```

**File:** crates/starknet_api/src/transaction_hash.rs (L375-392)
```rust
    let tip_resource_bounds_hash =
        get_tip_resource_bounds_hash(&transaction.resource_bounds(), transaction.tip())?;
    let paymaster_data_hash =
        HashChain::new().chain_iter(transaction.paymaster_data().0.iter()).get_poseidon_hash();
    let data_availability_mode = concat_data_availability_mode(
        transaction.nonce_data_availability_mode(),
        transaction.fee_data_availability_mode(),
    );
    let account_deployment_data_hash = HashChain::new()
        .chain_iter(transaction.account_deployment_data().0.iter())
        .get_poseidon_hash();
    let calldata_hash =
        HashChain::new().chain_iter(transaction.calldata().0.iter()).get_poseidon_hash();
    let mut hash_chain = HashChain::new()
        .chain(&INVOKE)
        .chain(&transaction_version.0)
        .chain(transaction.sender_address().0.key())
        .chain(&tip_resource_bounds_hash)
```

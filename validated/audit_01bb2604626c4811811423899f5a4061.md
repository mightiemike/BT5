### Title
`AllResources` → `L1Gas` Variant Collapse in Protobuf Round-Trip Produces Wrong Transaction Hash - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserialization of `ValidResourceBounds` uses a value-based heuristic to decide whether to reconstruct a `ValidResourceBounds::L1Gas` or `ValidResourceBounds::AllResources` variant. When an `AllResources` transaction carries zero `l2_gas` and `l1_data_gas` bounds, the round-trip silently collapses it to `L1Gas`. Because `get_tip_resource_bounds_hash` includes `L1_DATA_GAS` in the hash preimage only for `AllResources`, the two variants produce structurally different hashes. Any node that recomputes the hash from the deserialized transaction will derive a value that diverges from the hash the originating node computed and signed over, breaking the hash/signature binding for a class of valid user-submitted transactions.

### Finding Description

**Serialization side** (`From<ValidResourceBounds> for protobuf::ResourceBounds`, lines 471–490):

```rust
ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
    l1_gas: Some(l1_gas.into()),
    l2_gas: Some(value.get_l2_bounds().into()),      // returns ResourceBounds::default() → zeros
    l1_data_gas: Some(ResourceBounds::default().into()), // zeros
},
ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
    protobuf::ResourceBounds {
        l1_gas: Some(l1_gas.into()),
        l2_gas: Some(l2_gas.into()),
        l1_data_gas: Some(l1_data_gas.into()),
    },
```

When `AllResources` carries `l2_gas = 0` and `l1_data_gas = 0`, the wire bytes are byte-for-byte identical to those produced by `L1Gas`.

**Deserialization side** (`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, lines 417–436):

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← wrong variant for AllResources tx
} else {
    ValidResourceBounds::AllResources(...)
})
```

The variant is inferred purely from the wire values, with no tag or discriminant preserved.

**Hash divergence** (`get_tip_resource_bounds_hash`, lines 188–211):

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2-element hash
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3-element hash
    }
});
```

`L1Gas` hashes `[tip, L1_GAS_concat, L2_GAS_concat]`; `AllResources` hashes `[tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat]`. Even when `l1_data_gas = 0`, the packed felt `get_concat_resource(&ResourceBounds::default(), L1_DATA_GAS)` is non-zero (it encodes the resource tag `L1_DATA_GAS` in the upper bits), so the two poseidon digests are distinct.

### Impact Explanation

A user submits a valid `AllResources` (≥ v0.13.3) `InvokeV3`, `DeclareV3`, or `DeployAccountV3` transaction with `l2_gas = 0` and `l1_data_gas = 0`. The originating gateway computes and stores the hash using the `AllResources` formula (3-element preimage). When the transaction is propagated over P2P (block sync path via `FullTransaction` / `TransactionInBlock`, or mempool gossip via `MempoolTransaction`), the receiving node deserializes the resource bounds as `L1Gas` and recomputes the hash using the 2-element preimage. The recomputed hash differs from the transmitted hash. Any downstream hash verification (e.g., `validate_transaction_hash`) will reject the transaction, or — if verification is skipped — the wrong `GasVectorComputationMode` (`NoL2Gas` instead of `All`) is used for fee/gas accounting, producing incorrect execution results.

### Likelihood Explanation

The trigger is a valid, unprivileged user action: submitting a v3 transaction with zero `l2_gas` and `l1_data_gas` resource bounds. This is accepted by the gateway (the `AllResourceBounds` type imposes no non-zero constraint on these fields). The condition is reachable on mainnet today for any transaction that sets only L1 gas bounds while using the v0.13.3+ transaction format.

### Recommendation

Preserve the variant discriminant explicitly in the protobuf encoding. Add a boolean or enum tag (e.g., `bool all_resources = 4`) to `ResourceBounds` in the proto schema, set it to `true` when serializing `AllResources`, and use it — not the zero-value heuristic — to select the variant on deserialization. Alternatively, reject `AllResources` transactions with all-zero non-L1 bounds at the gateway so the ambiguous wire representation never enters the system.

### Proof of Concept

```rust
use starknet_api::transaction::fields::{
    AllResourceBounds, GasAmount, GasPrice, ResourceBounds, ValidResourceBounds,
};
use starknet_api::transaction_hash::get_tip_resource_bounds_hash;
use starknet_api::transaction::fields::Tip;
use apollo_protobuf::protobuf;

// Construct AllResources with zero l2_gas and l1_data_gas.
let all_resources = ValidResourceBounds::AllResources(AllResourceBounds {
    l1_gas: ResourceBounds { max_amount: GasAmount(100), max_price_per_unit: GasPrice(1) },
    l2_gas: ResourceBounds::default(),      // zero
    l1_data_gas: ResourceBounds::default(), // zero
});

// Compute hash as AllResources (3-element preimage).
let hash_all = get_tip_resource_bounds_hash(&all_resources, &Tip(0)).unwrap();

// Serialize to protobuf.
let proto: protobuf::ResourceBounds = all_resources.into();

// Deserialize: heuristic collapses to L1Gas because l2_gas==0 && l1_data_gas==0.
let roundtripped = ValidResourceBounds::try_from(proto).unwrap();
assert!(matches!(roundtripped, ValidResourceBounds::L1Gas(_))); // variant changed

// Compute hash as L1Gas (2-element preimage).
let hash_l1 = get_tip_resource_bounds_hash(&roundtripped, &Tip(0)).unwrap();

// Hashes diverge — the transaction hash the receiving node computes is wrong.
assert_ne!(hash_all, hash_l1);
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L471-490)
```rust
impl From<ValidResourceBounds> for protobuf::ResourceBounds {
    fn from(value: ValidResourceBounds) -> Self {
        match value {
            ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
                l1_gas: Some(l1_gas.into()),
                l2_gas: Some(value.get_l2_bounds().into()),
                l1_data_gas: Some(ResourceBounds::default().into()),
            },
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => protobuf::ResourceBounds {
                l1_gas: Some(l1_gas.into()),
                l2_gas: Some(l2_gas.into()),
                l1_data_gas: Some(l1_data_gas.into()),
            },
        }
    }
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

**File:** crates/starknet_api/src/transaction/fields.rs (L383-388)
```rust
    pub fn get_l2_bounds(&self) -> ResourceBounds {
        match self {
            Self::L1Gas(_) => ResourceBounds::default(),
            Self::AllResources(AllResourceBounds { l2_gas, .. }) => *l2_gas,
        }
    }
```

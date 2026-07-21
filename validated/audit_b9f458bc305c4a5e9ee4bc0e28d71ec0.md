### Title
`AllResources` V3 Transaction Silently Downgraded to `L1Gas` on Zero Bounds, Producing Wrong Transaction Hash — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf-to-internal conversion for `ValidResourceBounds` silently reclassifies a post-0.13.3 `AllResources` V3 transaction as a pre-0.13.3 `L1Gas` transaction whenever both `l2_gas` and `l1_data_gas` are zero. This is the direct sequencer analog of the original bug: just as the staking contract removed a user's record when two independent counters were simultaneously zero, this converter collapses a structurally distinct transaction type when two independent resource fields are simultaneously zero. The collapsed type produces a different transaction hash preimage (omitting the `L1DataGas` element), binding the wrong hash to the transaction.

### Finding Description

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs`:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← wrong variant
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

A V3 (`AllResources`) transaction with `l2_gas = 0` and `l1_data_gas = 0` is a valid, user-submittable transaction. After this conversion it becomes `ValidResourceBounds::L1Gas`, which is the pre-0.13.3 legacy variant.

The downstream hash function `get_tip_resource_bounds_hash` branches on the variant:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // ← L1DataGas omitted
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
``` [2](#0-1) 

For `L1Gas` the hash covers `[tip, L1Gas, L2Gas]` — three elements. For `AllResources` with the same zero values it covers `[tip, L1Gas, L2Gas(0), L1DataGas(0)]` — four elements. The Poseidon hash of three elements is cryptographically distinct from the hash of four elements, so the recomputed hash diverges from the original.

The same zero-condition downgrade is duplicated in the RPC layer:

```rust
impl From<ResourceBoundsMapping> for ValidResourceBounds {
    fn from(value: ResourceBoundsMapping) -> Self {
        if value.l1_data_gas.is_zero() && value.l2_gas.is_zero() {
            Self::L1Gas(value.l1_gas)
        } else { ... }
    }
}
``` [3](#0-2) 

Secondary effects of the wrong variant:

- `get_gas_vector_computation_mode` returns `NoL2Gas` instead of `All`, changing gas accounting. [4](#0-3) 
- `valid_resource_bounds_as_felts` returns 2 resource entries instead of 3, producing wrong syscall execution-info data. [5](#0-4) 
- `calculate_resource_bounds` (native execution path) returns only 2 entries for `L1Gas`, while the OS Cairo code asserts `n_resource_bounds = 3` for all V3 transactions. [6](#0-5) 

### Impact Explanation

**High. Transaction conversion or signature/hash logic binds the wrong hash type or executable payload.**

Any V3 transaction with `l2_gas.max_amount = 0`, `l2_gas.max_price_per_unit = 0`, `l1_data_gas.max_amount = 0`, `l1_data_gas.max_price_per_unit = 0` (all four fields zero) is affected. The transaction is accepted by the gateway as `AllResources` and its canonical hash is computed with the L1DataGas element. When the same transaction is later deserialized from protobuf (P2P sync) or from the RPC `ResourceBoundsMapping`, it is reclassified as `L1Gas` and its hash is recomputed without the L1DataGas element, producing a divergent value. This breaks hash validation during sync and causes wrong execution-info to be returned by RPC fee estimation and simulation for the affected transactions.

### Likelihood Explanation

Any unprivileged user can submit a V3 transaction with zero `l2_gas` and `l1_data_gas` bounds. The gateway does not reject such transactions — the `validate_tx_l2_gas_price_within_threshold` check is skipped entirely for the `L1Gas` arm, and the gateway accepts the transaction as `AllResources`. The condition is therefore trivially triggerable by any sender.

### Recommendation

Remove the value-based variant selection. The correct discriminant is the Starknet protocol version of the transaction, not the runtime values of the resource fields. For the protobuf path, always produce `AllResources` when all three resource-bound fields are present (even if zero), and produce `L1Gas` only when `l1_data_gas` is absent (the pre-0.13.3 wire format):

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas;
// ...
Ok(match l1_data_gas {
    None => ValidResourceBounds::L1Gas(l1_gas),
    Some(l1_data_gas) => ValidResourceBounds::AllResources(
        AllResourceBounds { l1_gas, l2_gas, l1_data_gas }
    ),
})
```

Apply the same fix to `From<ResourceBoundsMapping> for ValidResourceBounds` in the RPC layer.

### Proof of Concept

1. Construct a V3 Invoke transaction with `resource_bounds = { l1_gas: {max_amount: 1000, max_price: 1}, l2_gas: {max_amount: 0, max_price: 0}, l1_data_gas: {max_amount: 0, max_price: 0} }`.
2. Submit it to the gateway. It is accepted and its hash `H_orig` is computed via `get_tip_resource_bounds_hash` over `[tip, L1Gas, L2Gas(0), L1DataGas(0)]` — four elements — as `AllResources`. [7](#0-6) 
3. The transaction is included in a block and propagated via P2P. The receiving node deserializes the protobuf `ResourceBounds` message (which carries all three fields, all with zero l2/data values) through `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. [8](#0-7) 
4. The condition `l1_data_gas.is_zero() && l2_gas.is_zero()` is true; the converter returns `ValidResourceBounds::L1Gas(l1_gas)`.
5. `get_tip_resource_bounds_hash` is called on the deserialized transaction. It takes the `L1Gas` branch and hashes only `[tip, L1Gas, L2Gas(0)]` — three elements — producing `H_wrong ≠ H_orig`. [9](#0-8) 
6. Hash validation fails, or the wrong hash is stored/served, depending on the call site. Additionally, `get_gas_vector_computation_mode` returns `NoL2Gas` instead of `All`, silently altering gas accounting for the transaction. [4](#0-3)

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

**File:** crates/starknet_api/src/transaction_hash.rs (L188-210)
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
```

**File:** crates/apollo_rpc/src/v0_8/transaction.rs (L188-199)
```rust
impl From<ResourceBoundsMapping> for ValidResourceBounds {
    fn from(value: ResourceBoundsMapping) -> Self {
        if value.l1_data_gas.is_zero() && value.l2_gas.is_zero() {
            Self::L1Gas(value.l1_gas)
        } else {
            Self::AllResources(AllResourceBounds {
                l1_gas: value.l1_gas,
                l1_data_gas: value.l1_data_gas,
                l2_gas: value.l2_gas,
            })
        }
    }
```

**File:** crates/starknet_api/src/transaction/fields.rs (L333-349)
```rust
pub fn valid_resource_bounds_as_felts(
    resource_bounds: &ValidResourceBounds,
    exclude_l1_data_gas: bool,
) -> Result<Vec<ResourceAsFelts>, StarknetApiError> {
    let mut resource_bounds_vec: Vec<_> = vec![
        ResourceAsFelts::new(Resource::L1Gas, &resource_bounds.get_l1_bounds())?,
        ResourceAsFelts::new(Resource::L2Gas, &resource_bounds.get_l2_bounds())?,
    ];
    if exclude_l1_data_gas {
        return Ok(resource_bounds_vec);
    }
    if let ValidResourceBounds::AllResources(AllResourceBounds { l1_data_gas, .. }) =
        resource_bounds
    {
        resource_bounds_vec.push(ResourceAsFelts::new(Resource::L1DataGas, l1_data_gas)?)
    }
    Ok(resource_bounds_vec)
```

**File:** crates/starknet_api/src/transaction/fields.rs (L416-420)
```rust
    pub fn get_gas_vector_computation_mode(&self) -> GasVectorComputationMode {
        match self {
            Self::AllResources(_) => GasVectorComputationMode::All,
            Self::L1Gas(_) => GasVectorComputationMode::NoL2Gas,
        }
```

**File:** crates/blockifier/src/execution/native/utils.rs (L92-103)
```rust
    match tx_info.resource_bounds {
        ValidResourceBounds::L1Gas(_) => return res,
        ValidResourceBounds::AllResources(AllResourceBounds { l1_data_gas, .. }) => {
            if !exclude_l1_data_gas {
                res.push(ResourceBounds {
                    resource: Felt::from_hex(Resource::L1DataGas.to_hex()).unwrap(),
                    max_amount: l1_data_gas.max_amount.0,
                    max_price_per_unit: l1_data_gas.max_price_per_unit.0,
                })
            }
        }
    }
```

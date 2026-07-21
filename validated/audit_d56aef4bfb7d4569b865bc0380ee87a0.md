### Title
Protobuf `ValidResourceBounds` Deserialization Misclassifies `AllResources` as `L1Gas` When L2/L1DataGas Are Zero, Producing Wrong Transaction Hash - (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter uses a value-based heuristic — `l1_data_gas.is_zero() && l2_gas.is_zero()` — to decide between `ValidResourceBounds::L1Gas` (2-entry hash preimage) and `ValidResourceBounds::AllResources` (3-entry hash preimage). An `AllResources` transaction with zero L2 gas and zero L1DataGas bounds is incorrectly reconstructed as `L1Gas` after a protobuf round-trip. Because `get_tip_resource_bounds_hash` includes the L1DataGas entry in the hash preimage only for `AllResources`, the reconstructed transaction hash diverges from the original signed hash, causing signature verification failure and transaction rejection on any receiving P2P node.

---

### Finding Description

**The broken invariant (analog to the EMI last-cycle bug):** In the Teller bug, a time-based scaling factor was applied in the last payment cycle when it should not have been, causing under-calculation. Here, a value-based condition (`l2_gas.is_zero() && l1_data_gas.is_zero()`) selects the wrong hash variant for a specific class of valid transactions, causing the hash preimage to be truncated from 3 entries to 2 entries — the exact same "wrong branch taken for a boundary case" pattern.

**Root cause — protobuf converter:** [1](#0-0) 

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        // ...
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        // ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)          // ← 2-entry hash
        } else {
            ValidResourceBounds::AllResources(...)       // ← 3-entry hash
        })
    }
}
```

The condition conflates two structurally distinct transaction types:
- Pre-0.13.3 `L1Gas` transactions (signed over a 2-entry resource-bounds hash)
- Post-0.13.3 `AllResources` transactions where L2 gas and L1DataGas happen to be zero (signed over a 3-entry resource-bounds hash)

**Hash divergence — `get_tip_resource_bounds_hash`:** [2](#0-1) 

```rust
pub fn get_tip_resource_bounds_hash(resource_bounds: &ValidResourceBounds, tip: &Tip) -> Result<Felt, StarknetApiError> {
    // Always hashes L1_GAS and L2_GAS entries
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];
    // L1_DATA_GAS entry is added ONLY for AllResources
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],                          // 2 entries
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 entries
        }
    });
    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
```

**The `AllResources` path is always taken for RPC/internal transactions:** [3](#0-2) 

`InternalRpcInvokeTransactionV3::resource_bounds()` always returns `ValidResourceBounds::AllResources(self.resource_bounds)`, so the original hash is always computed over 3 entries.

**Serialization always emits all three fields:** [4](#0-3) 

When `ValidResourceBounds::AllResources` with zero L2/L1DataGas is serialized to protobuf, it emits `l2_gas: Some(zero)` and `l1_data_gas: Some(zero)`. On deserialization, both are zero, the heuristic fires, and `L1Gas` is returned — truncating the hash preimage.

**Gateway accepts `AllResources` with only L1 gas non-zero:** [5](#0-4) 

The `valid_l1_gas` test case confirms the gateway accepts `AllResourceBounds { l1_gas: NON_EMPTY_RESOURCE_BOUNDS, ..Default::default() }`, meaning L2 gas = 0 and L1DataGas = 0 is a valid, reachable input.

---

### Impact Explanation

Any `AllResources` transaction with zero L2 gas and zero L1DataGas bounds — a valid and accepted transaction type — will have its hash recomputed incorrectly by any node that receives it over P2P. The original signer signed over the 3-entry Poseidon hash; the receiving node computes a 2-entry hash. Signature verification fails and the transaction is rejected. This matches: **High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

---

### Likelihood Explanation

The trigger is fully unprivileged: any user can submit an `AllResources` invoke/declare/deploy-account transaction specifying only L1 gas bounds (L2 gas = 0, L1DataGas = 0). The gateway accepts it. Once the transaction enters the mempool and is propagated over P2P, every receiving node will misclassify it and reject it. No special permissions or malicious peer are required.

---

### Recommendation

Remove the value-based heuristic. The protobuf converter should not infer the resource-bounds variant from field values. Options:

1. **Always produce `AllResources`** when deserializing from protobuf (all modern V3 transactions use `AllResources`; pre-0.13.3 `L1Gas` transactions are not propagated over the current P2P protocol).
2. **Add a discriminator field** to the protobuf `ResourceBounds` message to explicitly encode the variant.
3. **Propagate the variant tag** through the conversion chain so the deserializer does not need to infer it.

---

### Proof of Concept

1. User constructs `RpcInvokeTransactionV3` with `resource_bounds = AllResourceBounds { l1_gas: {max_amount:100, max_price:1}, l2_gas: {0,0}, l1_data_gas: {0,0} }`.
2. Gateway computes hash **H** via `get_tip_resource_bounds_hash` with `AllResources` → 3-entry preimage: `[tip, L1_GAS_entry, L2_GAS_entry(0), L1_DATA_GAS_entry(0)]`.
3. User signs over **H**; transaction stored as `InternalRpcTransaction { tx_hash: H, ... }`.
4. Transaction is serialized to protobuf: `l2_gas = Some(zero)`, `l1_data_gas = Some(zero)`.
5. Receiving node deserializes: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas`.
6. Receiving node computes hash **H'** via `get_tip_resource_bounds_hash` with `L1Gas` → 2-entry preimage: `[tip, L1_GAS_entry, L2_GAS_entry(0)]`.
7. **H ≠ H'** → signature verification fails → transaction rejected.

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L471-489)
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L69-82)
```rust
#[rstest]
#[case::valid_l1_gas(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l1_gas: NON_EMPTY_RESOURCE_BOUNDS,
            ..Default::default()
        },
        ..Default::default()
    }
)]
```

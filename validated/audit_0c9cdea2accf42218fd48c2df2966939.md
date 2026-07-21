### Title
`ValidResourceBounds::AllResources(l2=0, l1_data=0)` Silently Downgraded to `L1Gas` in P2P Protobuf Deserializer, Breaking Transaction Hash Canonicalization on Syncing Nodes - (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

A V3 invoke transaction submitted with `AllResourceBounds(l1_gas=X, l2_gas=0, l1_data_gas=0)` has its transaction hash computed with three resource-bound elements (including `L1_DATA_GAS=0`). After the transaction is included in a block and synced to a peer via P2P protobuf, the protobuf deserializer silently converts the `AllResources` variant to `ValidResourceBounds::L1Gas` because both `l2_gas` and `l1_data_gas` are zero. The hash stored on the syncing node is the original H1 (three-element hash), but the stored transaction body now carries `L1Gas` (two-element hash). Any client that recomputes the hash from the RPC-served transaction body gets H2 ≠ H1, receiving an authoritative-looking wrong value.

### Finding Description

**Root cause — protobuf deserializer collapses `AllResources(l2=0, l1_data=0)` to `L1Gas`:** [1](#0-0) 

```rust
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← wrong for AllResources origin
} else {
    ValidResourceBounds::AllResources(...)
})
```

**Hash function produces different outputs for `L1Gas` vs `AllResources(l2=0, l1_data=0)`:**

`get_tip_resource_bounds_hash` conditionally appends the `L1_DATA_GAS` element only for `AllResources`: [2](#0-1) 

- `AllResources(l1=X, l2=0, l1_data=0)` → `poseidon(tip, L1_GAS(X), L2_GAS(0), L1_DATA_GAS(0))` — **4 elements** → hash **H1**
- `L1Gas(l1=X)` → `poseidon(tip, L1_GAS(X), L2_GAS(0))` — **3 elements** → hash **H2 ≠ H1**

**Serialization path preserves `AllResources` correctly (no information loss on the sending side):** [3](#0-2) 

For `AllResources(l2=0, l1_data=0)`, the serializer emits `l1_data_gas: Some(zero)` and `l2_gas: Some(zero)` — both fields are present. The deserializer then sees two zero values and collapses them to `L1Gas`, discarding the variant information.

**Hash is computed at the gateway on `InternalRpcInvokeTransactionV3` which always uses `AllResources`:** [4](#0-3) 

The hash H1 is stored alongside the transaction. On the syncing node the body is stored with `L1Gas` but the hash field retains H1.

**RPC conversion from `L1Gas` to `ResourceBoundsMapping` produces the same wire representation as `AllResources(l2=0, l1_data=0)`:** [5](#0-4) 

Both variants produce `{l1_gas: X, l1_data_gas: 0, l2_gas: 0}` in the RPC response. A client applying the standard reconstruction logic (zero l2 + zero l1_data → `L1Gas`) computes H2 ≠ H1.

### Impact Explanation

A syncing node's RPC returns a transaction body whose hash cannot be verified from the returned fields. Any client (bridge, wallet, explorer, proof verifier) that reconstructs the transaction hash from the RPC response will compute H2 and conclude the transaction is invalid or tampered, even though it is legitimate. This is a **High** impact: "RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."

### Likelihood Explanation

A user need only submit a V3 invoke with `l2_gas = 0` and `l1_data_gas = 0` — a structurally valid transaction accepted by the gateway. Every syncing peer that receives the block via P2P will silently store the wrong variant. The trigger is unprivileged and requires no special access.

### Recommendation

In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, do not collapse to `L1Gas` when `l1_data_gas` is explicitly present (even if zero). Distinguish the two cases by checking whether `l1_data_gas` was `None` (absent, meaning a pre-0.13.3 transaction) vs `Some(zero)` (present but zero, meaning a new `AllResources` transaction):

```rust
match value.l1_data_gas {
    None if l2_gas.is_zero() => Ok(ValidResourceBounds::L1Gas(l1_gas)),
    _ => Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })),
}
```

This preserves the variant information that the hash function depends on.

### Proof of Concept

1. Submit `RpcInvokeTransactionV3` with `resource_bounds = AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` to the gateway.
2. Gateway computes H1 = `poseidon(..., tip_resource_bounds_hash_AllResources, ...)` — four-element resource hash.
3. Transaction is included in a block; the block is propagated via P2P.
4. Syncing node deserializes `protobuf::InvokeV3` → `InvokeTrans

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

**File:** crates/starknet_api/src/transaction_hash.rs (L187-211)
```rust
// An implementation of the SNIP: https://github.com/EvyatarO/SNIPs/blob/snip-8/SNIPS/snip-8.md
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L388-393)
```rust
                )
            }
        };
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
    }
```

**File:** crates/apollo_rpc/src/v0_8/transaction.rs (L202-216)
```rust
impl From<ValidResourceBounds> for ResourceBoundsMapping {
    fn from(value: ValidResourceBounds) -> Self {
        match value {
            ValidResourceBounds::L1Gas(l1_gas) => Self {
                l1_gas,
                l1_data_gas: ResourceBounds::default(),
                l2_gas: ResourceBounds::default(),
            },
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l1_data_gas,
                l2_gas,
            }) => Self { l1_gas, l1_data_gas, l2_gas },
        }
    }
```

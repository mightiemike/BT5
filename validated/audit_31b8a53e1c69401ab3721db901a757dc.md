### Title
`AllResources` with zero L2/L1DataGas silently downgrades to `L1Gas` in protobuf round-trip, causing hash divergence and P2P rejection of valid declare transactions — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion uses a zero-value check on `l2_gas` and `l1_data_gas` to decide whether to reconstruct `ValidResourceBounds::L1Gas` or `ValidResourceBounds::AllResources`. An `AllResources` declare transaction whose `l2_gas` and `l1_data_gas` are both zero (a gateway-accepted configuration) is silently downgraded to `L1Gas` after a protobuf round-trip. Because `get_tip_resource_bounds_hash` hashes a different number of resource fields for each variant, the transaction hash changes. Separately, the `DeclareV3WithClass → RpcDeclareTransactionV3` conversion explicitly rejects `L1Gas`, so any receiving node that deserializes the propagated transaction over P2P will return `DEPRECATED_RESOURCE_BOUNDS_ERROR` and drop it.

---

### Finding Description

**Root cause — protobuf deserializer erases the `AllResources` variant when both optional bounds are zero:** [1](#0-0) 

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← type is changed
} else {
    ValidResourceBounds::AllResources(...)
})
```

The serializer writes `ResourceBounds::default()` for `l1_data_gas` when the source is `L1Gas`: [2](#0-1) 

So the round-trip `AllResources{l2=0, l1_data=0}` → protobuf → `L1Gas` is lossless from the wire perspective but lossy from the type perspective.

**Hash divergence — `get_tip_resource_bounds_hash` hashes a different number of elements per variant:** [3](#0-2) 

- `L1Gas` → poseidon(tip, l1_packed, l2_packed) — **2 resource felts**
- `AllResources` → poseidon(tip, l1_packed, l2_packed, l1_data_packed) — **3 resource felts**

A declare transaction submitted with `AllResourceBounds{l2=0, l1_data=0}` has its hash computed with 3 felts at the gateway. After P2P deserialization the same bytes produce `L1Gas`, so any hash recomputation uses only 2 felts — a different value.

**Admission rejection — `DeclareV3WithClass → RpcDeclareTransactionV3` hard-rejects `L1Gas`:** [4](#0-3) 

```rust
resource_bounds: match common.resource_bounds {
    ValidResourceBounds::AllResources(resource_bounds) => resource_bounds,
    _ => { return Err(DEPRECATED_RESOURCE_BOUNDS_ERROR); }
},
```

This is reached for every `MempoolTransaction::DeclareV3` received over P2P: [5](#0-4) 

**Gateway accepts the transaction** because `RpcDeclareTransactionV3` carries `resource_bounds: AllResourceBounds` directly (no `ValidResourceBounds` involved at ingress). The gateway stateless validator explicitly allows `AllResourceBounds` with only `l1_gas` non-zero: [6](#0-5) 

---

### Impact Explanation

A valid declare transaction with `AllResourceBounds{l1_gas=X, l2_gas=0, l1_data_gas=0}` is accepted by the gateway, assigned a hash H₁ (3-resource poseidon), and propagated over P2P. Every receiving sequencer node deserializes it as `L1Gas`, hits `DEPRECATED_RESOURCE_BOUNDS_ERROR`, and discards it. The transaction never enters any other node's mempool. If the originating node proposes a block containing it, peer validators cannot reconstruct the transaction and will reject the block, stalling consensus for that proposal round.

**Matching impact:** *High — Mempool/gateway/RPC admission rejects valid transactions before sequencing.*

---

### Likelihood Explanation

Any user who submits a declare transaction with only `l1_gas` set (a documented valid configuration, confirmed by the gateway test suite) and leaves `l2_gas` and `l1_data_gas` at their zero defaults triggers this path. No special privilege is required; the gateway RPC is the public entry point.

---

### Recommendation

The protobuf `ResourceBounds` message carries no type discriminator, so the deserializer cannot distinguish `AllResources{l2=0, l1_data=0}` from a legacy `L1Gas` transaction. Two options:

1. **Add a boolean/enum discriminator field** to `ResourceBounds` (e.g., `bool all_resources = 4`) and set it unconditionally when serializing `AllResources`, then use it — not the zero-check — to select the variant on deserialization.
2. **Remove the downgrade entirely** for the mempool P2P path: since `MempoolTransaction` only carries post-0.13.3 transactions, the deserializer for that message type should always produce `AllResources` (mirroring the `AllResourceBounds::try_from` path already used for `InvokeV3` and `DeployAccountV3`). [7](#0-6) 

---

### Proof of Concept

1. Craft a `RpcDeclareTransactionV3` with `resource_bounds = AllResourceBounds { l1_gas: <non-zero>, l2_gas: default(), l1_data_gas: default() }`.
2. Submit via the gateway RPC (`add_declare_transaction`). Gateway accepts; hash H₁ is computed via `get_declare_transaction_v3_hash` → `get_tip_resource_bounds_hash` with 3 resource felts.
3. The gateway serializes the transaction as `MempoolTransaction { txn: DeclareV3(DeclareV3WithClass { common: DeclareV3Common { resource_bounds: ResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 } } }) }` and broadcasts it over P2P.
4. A peer calls `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas(l1_gas)`.
5. `TryFrom<protobuf::DeclareV3WithClass> for RpcDeclareTransactionV3` matches `ValidResourceBounds::L1Gas` against the `_` arm → returns `DEPRECATED_RESOURCE_BOUNDS_ERROR`.
6. The peer discards the transaction. The originating node's block proposal referencing H₁ is rejected by all peers.

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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L32-48)
```rust
impl TryFrom<protobuf::MempoolTransaction> for RpcTransaction {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::MempoolTransaction) -> Result<Self, Self::Error> {
        let txn = value.txn.ok_or(missing("RpcTransaction::txn"))?;
        Ok(match txn {
            protobuf::mempool_transaction::Txn::DeclareV3(txn) => {
                RpcTransaction::Declare(RpcDeclareTransaction::V3(txn.try_into()?))
            }
            protobuf::mempool_transaction::Txn::DeployAccountV3(txn) => {
                RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(txn.try_into()?))
            }
            protobuf::mempool_transaction::Txn::InvokeV3(txn) => {
                RpcTransaction::Invoke(RpcInvokeTransaction::V3(txn.try_into()?))
            }
        })
    }
}
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L169-192)
```rust
impl TryFrom<protobuf::DeclareV3WithClass> for RpcDeclareTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::DeclareV3WithClass) -> Result<Self, Self::Error> {
        let (common, class) = value.try_into()?;
        Ok(Self {
            resource_bounds: match common.resource_bounds {
                ValidResourceBounds::AllResources(resource_bounds) => resource_bounds,
                _ => {
                    return Err(DEPRECATED_RESOURCE_BOUNDS_ERROR);
                }
            },
            sender_address: common.sender_address,
            signature: common.signature,
            nonce: common.nonce,
            compiled_class_hash: common.compiled_class_hash,
            contract_class: class,
            tip: common.tip,
            paymaster_data: common.paymaster_data,
            account_deployment_data: common.account_deployment_data,
            nonce_data_availability_mode: common.nonce_data_availability_mode,
            fee_data_availability_mode: common.fee_data_availability_mode,
        })
    }
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

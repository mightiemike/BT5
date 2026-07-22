### Title
`AllResources` V3 Invoke Transaction with Zero L2/L1-Data-Gas Bounds Silently Downcasts to `L1Gas` on P2P Protobuf Deserialization, Causing Transaction Loss and Hash Domain Collision — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` classifies any wire message whose `l2_gas` and `l1_data_gas` fields are both zero as `ValidResourceBounds::L1Gas`, regardless of whether the sender originally encoded an `AllResources` variant. A V3 invoke transaction submitted to the gateway with `AllResourceBounds { l1_gas: non-zero, l2_gas: {0,0}, l1_data_gas: {0,0} }` is accepted and hashed under the `AllResources` domain (three resource-bound felts in the Poseidon preimage). When that transaction is later serialized to protobuf and received by any peer, the deserializer produces `ValidResourceBounds::L1Gas`, which (a) causes the `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` conversion to hard-fail with `DEPRECATED_RESOURCE_BOUNDS_ERROR`, and (b) would produce a different transaction hash if re-hashed, because `get_tip_resource_bounds_hash` omits the `L1_DATA_GAS` felt for the `L1Gas` variant. The transaction is therefore permanently unsequenceable: accepted at the gateway, stuck in the originating mempool, and rejected by every peer.

---

### Finding Description

**Step 1 — Serialization (sender side)**

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds` directly. When the P2P propagator serializes it, the path is:

```
RpcInvokeTransactionV3 → InvokeTransactionV3 → protobuf::InvokeV3
```

`From<RpcInvokeTransactionV3> for protobuf::InvokeV3WithProof` calls `value.into()` to obtain an `InvokeTransactionV3`, which wraps the bounds as `ValidResourceBounds::AllResources(...)`. The subsequent `From<ValidResourceBounds> for protobuf::ResourceBounds` serializes all three fields:

```rust
// crates/apollo_protobuf/src/converters/transaction.rs
ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
    protobuf::ResourceBounds {
        l1_gas: Some(l1_gas.into()),
        l2_gas: Some(l2_gas.into()),       // {0, 0}
        l1_data_gas: Some(l1_data_gas.into()), // {0, 0}
    },
```

The wire bytes for `AllResources{l1_gas: X, l2_gas: {0,0}, l1_data_gas: {0,0}}` are **identical** to the wire bytes for `L1Gas{l1_gas: X}` with default-zero padding.

**Step 2 — Deserialization (receiver side)**

```rust
// crates/apollo_protobuf/src/converters/transaction.rs  lines 431-435
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← wrong variant
} else {
    ValidResourceBounds::AllResources(...)
})
```

`ResourceBounds::is_zero()` returns `true` when both `max_amount == 0` and `max_price_per_unit == 0`. A user-supplied `AllResources` transaction with zero L2/L1-data-gas bounds satisfies this condition, so the receiver reconstructs `ValidResourceBounds::L1Gas`.

**Step 3 — Conversion failure**

The receiver then calls:

```rust
// crates/apollo_protobuf/src/converters/rpc_transaction.rs  line 130
Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
```

`snapi_invoke.try_into()` invokes:

```rust
// crates/starknet_api/src/rpc_transaction.rs  lines 591-597
resource_bounds: match value.resource_bounds {
    ValidResourceBounds::AllResources(bounds) => bounds,
    _ => {
        return Err(StarknetApiError::OutOfRange { string: "resource_bounds".to_string() });
    }
},
```

Because the variant is now `L1Gas`, this arm fires and the entire deserialization returns `DEPRECATED_RESOURCE_BOUNDS_ERROR`. The peer rejects the transaction.

**Step 4 — Hash domain collision (secondary)**

Even if the conversion were patched to not fail, the transaction hash computed at the gateway under `AllResources` differs from the hash that would be computed under `L1Gas`. `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` felt only for `AllResources`:

```rust
// crates/starknet_api/src/transaction_hash.rs  lines 203-208
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2-felt preimage
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3-felt preimage
    }
});
```

`AllResources` hash preimage: `[tip, L1_GAS, L2_GAS=0, L1_DATA_GAS=0]`
`L1Gas` hash preimage: `[tip, L1_GAS, L2_GAS=0]`

These produce distinct Poseidon digests, so any node that re-hashes after deserialization would compute a different `tx_hash`, causing a second rejection path.

---

### Impact Explanation

A valid V3 invoke transaction accepted by the gateway — with non-zero L1 gas bounds and zero L2/L1-data-gas bounds — cannot be propagated to any peer. Every receiving node either hard-fails the protobuf conversion (`DEPRECATED_RESOURCE_BOUNDS_ERROR`) or, if that path were fixed, would compute a mismatched transaction hash. The transaction is permanently stuck: it occupies a mempool slot on the originating node, consumes the sender's nonce slot, and can never be included in a block. This matches the **High** impact scope: *"Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."*

---

### Likelihood Explanation

Any user or SDK that constructs a V3 invoke transaction with `AllResourceBounds` where `l2_gas` and `l1_data_gas` are both `{max_amount: 0, max_price_per_unit: 0}` triggers this path. This is a natural configuration for a user who only wants to bound L1 gas (e.g., migrating from a pre-0.13.3 flow). The gateway imposes no check preventing zero L2/L1-data-gas bounds. Likelihood is **Medium**.

---

### Recommendation

Replace the heuristic downcast in `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` with an explicit discriminator field in the protobuf schema, or always deserialize as `AllResources` when all three resource fields are present on the wire (regardless of their values). The zero-value check was designed to distinguish legacy 0.13.2 messages (which omit `l1_data_gas` entirely) from new ones, but it incorrectly conflates a missing field with an explicitly-zero field. The fix should be:

```rust
// Treat absent l1_data_gas as legacy L1Gas; treat present-but-zero as AllResources.
Ok(if value.l1_data_gas.is_none() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

This preserves backward compatibility with 0.13.2 peers (which never send `l1_data_gas`) while correctly round-tripping `AllResources` transactions with zero bounds.

---

### Proof of Concept

1. Construct a V3 invoke transaction with:
   ```
   resource_bounds = AllResourceBounds {
       l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
       l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
       l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
   }
   ```
2. Submit to the gateway. It is accepted; the hash H₁ is computed via `get_invoke_transaction_v3_hash` with a 3-felt resource preimage (L1_GAS, L2_GAS=0, L1_DATA_GAS=0).
3. The originating node propagates the transaction as a `ConsensusTransaction::RpcTransaction` over P2P. `From<RpcInvokeTransactionV3> for protobuf::InvokeV3WithProof` serializes `resource_bounds` with all three fields present and zero for L2/L1-data-gas.
4. A peer receives the protobuf message. `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` fires the `l1_data_gas.is_zero() && l2_gas.is_zero()` branch and produces `ValidResourceBounds::L1Gas`.
5. `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` hits the `_ => Err(OutOfRange)` arm; the caller maps this to `DEPRECATED_RESOURCE_BOUNDS_ERROR`; the peer drops the transaction.
6. Independently: re-hashing the deserialized transaction under `L1Gas` via `get_tip_resource_bounds_hash` produces hash H₂ ≠ H₁ (2-felt vs 3-felt preimage), confirming the hash domain collision.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L115-131)
```rust
impl TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(mut value: protobuf::InvokeV3WithProof) -> Result<Self, Self::Error> {
        // Extract proof first, since `starknet_api::transaction::InvokeTransactionV3` does not
        // carry a `proof` field.
        let proof = Proof::from(std::mem::take(&mut value.proof));

        let snapi_invoke: InvokeTransactionV3 = value
            .invoke
            .ok_or(ProtobufConversionError::MissingField {
                field_description: "InvokeV3WithProof::invoke",
            })?
            .try_into()?;

        // This conversion can fail only if the resource_bounds are not AllResources.
        Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L586-611)
```rust
impl TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3 {
    type Error = StarknetApiError;

    fn try_from(value: InvokeTransactionV3) -> Result<Self, Self::Error> {
        Ok(Self {
            resource_bounds: match value.resource_bounds {
                ValidResourceBounds::AllResources(bounds) => bounds,
                _ => {
                    return Err(StarknetApiError::OutOfRange {
                        string: "resource_bounds".to_string(),
                    });
                }
            },
            signature: value.signature,
            nonce: value.nonce,
            tip: value.tip,
            paymaster_data: value.paymaster_data,
            nonce_data_availability_mode: value.nonce_data_availability_mode,
            fee_data_availability_mode: value.fee_data_availability_mode,
            sender_address: value.sender_address,
            calldata: value.calldata,
            account_deployment_data: value.account_deployment_data,
            proof_facts: value.proof_facts,
            proof: Proof::default(),
        })
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

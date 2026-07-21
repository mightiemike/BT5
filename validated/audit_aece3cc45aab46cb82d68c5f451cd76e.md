### Title
Protobuf `ValidResourceBounds` round-trip silently downcasts `AllResources{l2=0, l1_data=0}` to `L1Gas`, causing valid gateway-accepted transactions to be rejected during P2P mempool propagation — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a zero-value heuristic to reconstruct the original variant. An `AllResources` V3 invoke transaction with zero L2-gas and zero L1-data-gas bounds — accepted by the gateway — is silently converted to `ValidResourceBounds::L1Gas` after a protobuf round-trip. The subsequent conversion back to `RpcInvokeTransactionV3` then fails because that type requires `AllResources`, causing the receiving peer to reject the transaction with `DEPRECATED_RESOURCE_BOUNDS_ERROR`. The transaction is never propagated beyond the originating node.

---

### Finding Description

**The invariant that breaks:** `AllResources{l1_gas: X, l2_gas: 0, l1_data_gas: 0}` and `L1Gas(X)` are semantically distinct variants — they produce different transaction hashes via `get_tip_resource_bounds_hash` — but the protobuf deserializer collapses them to the same value.

**Step 1 — Gateway accepts the transaction.**

`InternalRpcInvokeTransactionV3` stores `resource_bounds: AllResourceBounds` and its `InvokeTransactionV3Trait::resource_bounds()` implementation always returns `ValidResourceBounds::AllResources(self.resource_bounds)`. [1](#0-0) 

The gateway's stateless validator imposes no lower bound on L2-gas or L1-data-gas amounts, so `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is accepted.

**Step 2 — Hash is computed using `AllResources` (3-element hash).**

`get_tip_resource_bounds_hash` hashes a different number of resource elements depending on the variant: [2](#0-1) 

For `AllResources`, the hash input is `[tip, L1_GAS_packed, L2_GAS_packed, L1_DATA_GAS_packed]` (3 resource felts). For `L1Gas`, it is `[tip, L1_GAS_packed, L2_GAS_packed]` (2 resource felts). These produce different `tip_resource_bounds_hash` values even when L2 and L1-data amounts are zero.

**Step 3 — Protobuf serialization of the mempool P2P message.**

The mempool P2P propagation path serializes `RpcInvokeTransactionV3` → `InvokeTransactionV3` (wrapping in `AllResources`) → `protobuf::InvokeV3`: [3](#0-2) 

**Step 4 — Protobuf deserialization collapses the variant.**

On the receiving peer, `protobuf::InvokeV3` → `InvokeTransactionV3` calls `ValidResourceBounds::try_from(protobuf::ResourceBounds)`: [4](#0-3) 

When `l2_gas.is_zero() && l1_data_gas.is_zero()`, the result is `ValidResourceBounds::L1Gas(l1_gas)` — the `AllResources` variant is lost.

**Step 5 — Conversion back to `RpcInvokeTransactionV3` fails.**

`TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` calls `snapi_invoke.try_into()` which invokes `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3`: [5](#0-4) 

Because `resource_bounds` is now `L1Gas`, not `AllResources`, the match arm falls to the error branch and returns `DEPRECATED_RESOURCE_BOUNDS_ERROR`: [6](#0-5) 

The receiving peer rejects the transaction. It is never added to that peer's mempool.

---

### Impact Explanation

**Impact: High — Mempool/gateway/RPC admission rejects valid transactions before sequencing.**

A valid V3 invoke transaction accepted by the gateway is silently dropped at every peer that receives it via P2P mempool propagation. The transaction can only be sequenced by the originating node. If that node is not the current block proposer, the transaction is effectively lost from the network. An attacker can exploit this to ensure their own transactions are sequenced while a victim's transactions (with zero L2/L1-data gas) are never propagated.

---

### Likelihood Explanation

Any V3 invoke transaction with `l2_gas.max_amount = 0` and `l1_data_gas.max_amount = 0` (and both `max_price_per_unit = 0`) triggers this path. The gateway's stateless validator explicitly allows zero L2-gas amounts. This is a deterministic, unprivileged trigger requiring only a standard RPC submission.

---

### Recommendation

1. **Fix the deserializer heuristic.** The protobuf schema for `ResourceBounds` should carry an explicit discriminant (e.g., a boolean `is_all_resources` field, or a `oneof`) so the deserializer can reconstruct the correct variant without relying on zero-value inference.

2. **Alternatively, widen the `TryFrom` conversion.** `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` should accept `ValidResourceBounds::L1Gas` and convert it to `AllResourceBounds { l1_gas, l2_gas: default, l1_data_gas: default }` rather than returning an error, since the gateway already accepts such transactions.

3. **Add a round-trip test** for `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` through the full protobuf mempool P2P path to catch this regression.

---

### Proof of Concept

```
1. Submit via RPC gateway:
   RpcInvokeTransactionV3 {
     resource_bounds: AllResourceBounds {
       l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
       l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
       l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     },
     ...
   }

2. Gateway accepts: stateless validator passes (no minimum on l2/l1_data gas).
   Hash H1 computed using AllResources (3-element resource hash).

3. Mempool propagates via P2P:
   RpcInvokeTransactionV3
     → InvokeTransactionV3 { resource_bounds: AllResources(1000, 0, 0) }
     → protobuf::InvokeV3 { resource_bounds: { l1_gas: 1000, l2_gas: 0, l1_data_gas: 0 } }

4. Receiving peer deserializes:
   protobuf::ResourceBounds { l1_gas: 1000, l2_gas: 0, l1_data_gas: 0 }
     → l1_data_gas.is_zero() && l2_gas.is_zero() == true
     → ValidResourceBounds::L1Gas(1000)   ← variant changed

5. TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3:
   match resource_bounds {
     ValidResourceBounds::AllResources(b) => b,  // not taken
     _ => return Err(OutOfRange)                  // taken → DEPRECATED_RESOURCE_BOUNDS_ERROR
   }

6. Transaction rejected at receiving peer. Never enters its mempool.
   Hash H2 (if recomputed) would use L1Gas (2-element resource hash) ≠ H1.
```

### Citations

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L115-132)
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
}
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L134-144)
```rust
impl From<RpcInvokeTransactionV3> for protobuf::InvokeV3WithProof {
    fn from(mut value: RpcInvokeTransactionV3) -> Self {
        // Extract proof first, since `starknet_api::transaction::InvokeTransactionV3` does not
        // carry a `proof` field.
        let proof = Arc::unwrap_or_clone(std::mem::take(&mut value.proof).0);

        let snapi_invoke: InvokeTransactionV3 = value.into();

        Self { invoke: Some(snapi_invoke.into()), proof }
    }
}
```

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

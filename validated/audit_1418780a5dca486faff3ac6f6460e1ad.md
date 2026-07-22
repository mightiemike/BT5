### Title
`ValidResourceBounds` Protobuf Round-Trip Silently Downcasts `AllResources` to `L1Gas`, Causing Transaction Hash Divergence and Cross-Node Rejection — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserialization of `ValidResourceBounds` uses a value-based heuristic to decide which variant to reconstruct. When `l2_gas` and `l1_data_gas` are both zero, it silently produces `ValidResourceBounds::L1Gas` even if the sender transmitted an `AllResources` transaction. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` term in the hash only for `AllResources`, the two variants produce structurally different hash preimages. A valid V3 transaction accepted by the gateway under the `AllResources` hash is therefore either permanently rejected or bound to a different hash on every peer that receives it over P2P.

---

### Finding Description

**Step 1 — Submission path (gateway side)**

`RpcInvokeTransactionV3` always carries `AllResourceBounds` (never `L1Gas`). A user may legitimately submit a V3 invoke with only L1 gas set and both L2 gas and L1 data gas at zero:

```
AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }
```

The gateway converts this to `InternalRpcInvokeTransactionV3`, which also stores `AllResourceBounds`. The hash is computed via `get_invoke_transaction_v3_hash` → `get_tip_resource_bounds_hash`:

```
resource_felts = [concat(X, L1_GAS), concat(0, L2_GAS), concat(0, L1_DATA_GAS)]
hash_A = Poseidon(tip, concat(X,L1_GAS), concat(0,L2_GAS), concat(0,L1_DATA_GAS))
```

The transaction is stored in the mempool under `hash_A`.

**Step 2 — Protobuf serialization (outbound P2P)**

`RpcInvokeTransactionV3 → InvokeTransactionV3 → protobuf::InvokeV3` serializes `ValidResourceBounds::AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` as three wire fields, all present, with l2_gas and l1_data_gas both zero.

**Step 3 — Protobuf deserialization (receiving node)**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` applies the heuristic:

```rust
// crates/apollo_protobuf/src/converters/transaction.rs  line 431
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← variant is changed
} else {
    ValidResourceBounds::AllResources(...)
})
```

The receiving node reconstructs `ValidResourceBounds::L1Gas(X)`.

**Step 4 — Hash divergence**

`get_tip_resource_bounds_hash` branches on the variant:

```rust
// crates/starknet_api/src/transaction_hash.rs  lines 203-208
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],          // L1_DATA_GAS omitted
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
```

The receiving node computes:

```
hash_B = Poseidon(tip, concat(X,L1_GAS), concat(0,L2_GAS))
```

`hash_A ≠ hash_B` because `hash_A` has three resource terms and `hash_B` has two.

**Step 5 — Hard rejection**

Before the hash divergence can even manifest, the receiving node's deserialization pipeline fails entirely. `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` calls:

```rust
// crates/apollo_protobuf/src/converters/rpc_transaction.rs  line 130
Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
```

`snapi_invoke.try_into()` is `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3`, which rejects any non-`AllResources` variant:

```rust
// crates/starknet_api/src/rpc_transaction.rs  lines 591-597
resource_bounds: match value.resource_bounds {
    ValidResourceBounds::AllResources(bounds) => bounds,
    _ => {
        return Err(StarknetApiError::OutOfRange { string: "resource_bounds".to_string() });
    }
},
```

The transaction is dropped with `DEPRECATED_RESOURCE_BOUNDS_ERROR` on every receiving peer. The transaction can never be sequenced.

---

### Impact Explanation

A valid V3 invoke transaction — one that passes all gateway stateless and stateful validation — is permanently unsequenceable. Every peer that receives it over P2P silently discards it because the protobuf round-trip changes the `ValidResourceBounds` variant, triggering a hard error in the deserialization path. The submitter receives a successful gateway response (the transaction is accepted and assigned `hash_A`) but the transaction never reaches the batcher on any node. This matches the impact category: **"Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing"** and **"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."**

---

### Likelihood Explanation

The trigger condition — a V3 invoke with non-zero L1 gas but zero L2 gas and zero L1 data gas — is a normal, user-constructable transaction. The gateway's stateless validator explicitly accepts it (test case `valid_l1_gas` passes with only `l1_gas` non-zero). No privileged access is required. Any user who submits such a transaction will experience silent, permanent loss of that transaction.

---

### Recommendation

The protobuf deserializer must not use a value-based heuristic to select the `ValidResourceBounds` variant. Two options:

1. **Add an explicit discriminant field** to the protobuf `ResourceBounds` message that encodes whether the sender intended `L1Gas` or `AllResources`, and use that field during deserialization.
2. **Always produce `AllResources`** when all three resource fields are present on the wire (regardless of whether l2_gas and l1_data_gas are zero), reserving `L1Gas` only for messages that genuinely omit the l2_gas field (i.e., pre-0.13.3 peers that never send it).

The second option is the minimal fix and aligns with the existing TODO comment:

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
```

Once 0.13.2 support is dropped, `l1_data_gas` being absent becomes the sole signal for `L1Gas`; zero-valued-but-present fields must map to `AllResources`.

---

### Proof of Concept

```
1. Construct a V3 invoke transaction:
     sender_address = <any valid account>
     resource_bounds = AllResourceBounds {
         l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     }
     (all other fields: valid nonce, signature, etc.)

2. Submit via JSON-RPC starknet_addInvokeTransaction.
   → Gateway accepts; returns tx_hash = hash_A (computed with AllResources, 3 resource terms).

3. The gateway propagates the transaction to peers via P2P protobuf (InvokeV3WithProof).

4. On the receiving peer:
   protobuf::ResourceBounds { l1_gas: 1000/1, l2_gas: 0/0, l1_data_gas: 0/0 }
   → TryFrom<protobuf::ResourceBounds> for ValidResourceBounds
   → l1_data_gas.is_zero() && l2_gas.is_zero() == true
   → ValidResourceBounds::L1Gas(l1_gas)          ← variant changed

5. TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3:
   resource_bounds is L1Gas, not AllResources
   → returns Err(OutOfRange { "resource_bounds" })
   → mapped to DEPRECATED_RESOURCE_BOUNDS_ERROR
   → transaction silently dropped

6. The transaction is never delivered to any batcher.
   hash_A is permanently orphaned.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L586-612)
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
}
```

### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Causing Valid Transactions to Be Rejected in P2P Mempool Propagation — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion uses value-based discrimination to reconstruct the `ValidResourceBounds` variant: if both `l1_data_gas` and `l2_gas` are zero, it unconditionally produces `ValidResourceBounds::L1Gas`, even when the original transaction was submitted as `AllResources`. A valid `RpcInvokeTransactionV3` with `AllResourceBounds { l2_gas: 0, l1_data_gas: 0 }` is accepted by the gateway (which always uses `AllResourceBounds`), serialized to protobuf with all three resource fields present, but on the receiving P2P node the deserialization silently downgrades it to `L1Gas`. The subsequent conversion `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` then hard-fails because it requires `AllResources`, causing the transaction to be rejected by every P2P peer.

### Finding Description

**Step 1 – Gateway accepts the transaction.**

`RpcInvokeTransactionV3` uses `AllResourceBounds` (not `ValidResourceBounds`), so the gateway always computes the hash with the `AllResources` variant. `get_invoke_transaction_v3_hash` includes `L1_DATA_GAS_concat` in the hash even when its value is zero: [1](#0-0) 

The hash is therefore bound to the `AllResources` domain.

**Step 2 – Serialization to protobuf preserves all three fields.**

`From<RpcInvokeTransactionV3> for protobuf::InvokeV3WithProof` converts via `InvokeTransactionV3` (which carries `ValidResourceBounds::AllResources`) and then to `protobuf::InvokeV3`. The `l1_data_gas` field is emitted in the wire message even when its value is zero: [2](#0-1) 

**Step 3 – Deserialization silently downgrades the variant.**

On the receiving node, `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` calls `ValidResourceBounds::try_from(protobuf::ResourceBounds)`. The conversion uses a value test, not field-presence, to choose the variant: [3](#0-2) 

When `l1_data_gas = 0` and `l2_gas = 0`, the result is `ValidResourceBounds::L1Gas(l1_gas)` — the `AllResources` identity is lost.

**Step 4 – Conversion to `RpcInvokeTransactionV3` hard-fails.**

`TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` then calls `snapi_invoke.try_into()`, which is `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3`. That conversion rejects any variant other than `AllResources`: [4](#0-3) 

The error is mapped to `DEPRECATED_RESOURCE_BOUNDS_ERROR` and propagated: [5](#0-4) 

The transaction is rejected by every P2P peer that receives it.

**Step 5 – Hash domain divergence (secondary impact).**

The same downgrade occurs in the P2P sync path (`TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` is shared). A syncing node stores the transaction with `L1Gas` resource bounds. If the hash is ever recomputed from the stored object (e.g., OS re-execution, echonet), `get_tip_resource_bounds_hash` omits `L1_DATA_GAS_concat` and produces a different hash than the one originally signed and stored: [6](#0-5) 

### Impact Explanation

**High — Mempool/gateway/RPC admission rejects valid transactions before sequencing.**

Any `InvokeV3` transaction with `AllResourceBounds { l2_gas: 0, l1_data_gas: 0 }` is accepted by the originating gateway but silently dropped by every P2P peer. In a distributed sequencer topology the transaction never reaches the batcher of any other node, so it can only be sequenced by the single node that received it directly. This is an unprivileged, externally triggerable rejection of a structurally valid transaction.

Secondary: in the P2P sync path the stored `resource_bounds` variant is wrong, causing hash recomputation divergence that would break OS re-execution and proof verification for those transactions.

### Likelihood Explanation

The trigger condition (`l2_gas = 0` and `l1_data_gas = 0`) is reachable by any user who submits an `AllResources` transaction specifying only L1 gas (a natural pattern for users migrating from pre-0.13.3 semantics or explicitly setting the other bounds to zero). No privileged access is required. The gateway accepts the transaction; the bug fires on every P2P peer that subsequently receives it.

### Recommendation

Replace the value-based variant selection in `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` with field-presence discrimination: if the `l1_data_gas` field is present in the protobuf message (i.e., `value.l1_data_gas.is_some()`), produce `AllResources`; if it is absent, produce `L1Gas`. This preserves the original variant regardless of the numeric values of the bounds.

```rust
// Proposed fix
Ok(if let Some(l1_data_gas_proto) = value.l1_data_gas {
    let l1_data_gas: ResourceBounds = l1_data_gas_proto.try_into()?;
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
} else {
    ValidResourceBounds::L1Gas(l1_gas)
})
```

The TODO comment already anticipates this fix: `// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.` [7](#0-6) 

### Proof of Concept

1. Construct an `RpcInvokeTransactionV3` with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` and a valid signature over the `AllResources` hash (which includes the zero `L1_DATA_GAS_concat` element).
2. Submit it to the gateway. It is accepted; `InternalRpcInvokeTransactionV3` stores `AllResourceBounds` and the hash is computed with `AllResources` semantics.
3. The mempool P2P propagator serializes it: `From<RpcInvokeTransactionV3> for protobuf::InvokeV3WithProof` emits `l1_data_gas: Some(0)` in the wire message.
4. A peer receives the message and calls `TryFrom<protobuf::MempoolTransaction> for RpcTransaction` → `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` → `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3`.
5. Inside step 4, `ValidResourceBounds::try_from(ResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 })` evaluates `l1_data_gas.is_zero() && l2_gas.is_zero()` as `true` and returns `L1Gas(X)`.
6. `snapi_invoke.try_into::<RpcInvokeTransactionV3>()` matches `L1Gas` against the `AllResources`-only arm, returns `Err(OutOfRange)`, mapped to `DEPRECATED_RESOURCE_BOUNDS_ERROR`.
7. The transaction is rejected by the peer; it never enters that node's mempool or batcher. [8](#0-7) [9](#0-8)

### Citations

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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L115-143)
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

impl From<RpcInvokeTransactionV3> for protobuf::InvokeV3WithProof {
    fn from(mut value: RpcInvokeTransactionV3) -> Self {
        // Extract proof first, since `starknet_api::transaction::InvokeTransactionV3` does not
        // carry a `proof` field.
        let proof = Arc::unwrap_or_clone(std::mem::take(&mut value.proof).0);

        let snapi_invoke: InvokeTransactionV3 = value.into();

        Self { invoke: Some(snapi_invoke.into()), proof }
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

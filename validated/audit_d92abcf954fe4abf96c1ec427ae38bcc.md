### Title
Protobuf `ValidResourceBounds` Canonicalization Collapses `AllResources` to `L1Gas`, Producing a Wrong Hash Domain and Causing Consensus Proposal Rejection - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` silently downgrades an `AllResources` variant to `L1Gas` whenever `l2_gas` and `l1_data_gas` are both zero. Because `get_tip_resource_bounds_hash` includes `L1_DATA_GAS` in the Poseidon preimage only for `AllResources`, the same transaction fields produce two distinct hashes depending on which path is used. Additionally, the downstream `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` conversion hard-fails on `L1Gas`, so any consensus proposal that contains such a transaction is unconditionally rejected by every validator that receives it over P2P.

---

### Finding Description

**Step 1 – Gateway accepts the transaction and computes hash H₁ (AllResources path)**

A user submits an `RpcInvokeTransactionV3` with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`. The gateway converts it via `convert_rpc_tx_to_internal`, which calls `InternalRpcInvokeTransactionV3::resource_bounds()` → always returns `ValidResourceBounds::AllResources(...)`. `get_tip_resource_bounds_hash` therefore appends the `L1_DATA_GAS` felt (non-zero even for zero bounds, because the resource-name prefix `b"L1_DATA"` is packed in) to the Poseidon chain, producing hash **H₁**. [1](#0-0) [2](#0-1) 

**Step 2 – Proposer broadcasts the transaction via protobuf**

`RpcInvokeTransactionV3 → InvokeTransactionV3` always wraps the bounds as `ValidResourceBounds::AllResources`. The `InvokeTransactionV3 → protobuf::InvokeV3` serializer emits `l2_gas = 0` and `l1_data_gas = 0` on the wire. [3](#0-2) 

**Step 3 – Validator deserializes and the bounds variant is silently downgraded**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` checks `l1_data_gas.is_zero() && l2_gas.is_zero()` and returns `ValidResourceBounds::L1Gas(l1_gas)` instead of `AllResources`. [4](#0-3) 

**Step 4 – Conversion to `RpcInvokeTransactionV3` hard-fails**

`TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` rejects any variant that is not `AllResources`, mapping the error to `DEPRECATED_RESOURCE_BOUNDS_ERROR`. [5](#0-4) [6](#0-5) 

**Step 5 – Proposal is unconditionally rejected**

`handle_proposal_part` collects all conversion results; a single `Err` causes `HandledProposalPart::Failed`, terminating the proposal. [7](#0-6) 

**Hash-domain divergence (secondary impact)**

Even if the conversion were made to succeed, the hash computed at the gateway (H₁, `AllResources`, includes `L1_DATA_GAS` felt) would differ from any hash recomputed after protobuf round-trip (H₂, `L1Gas`, omits `L1_DATA_GAS` felt). The two hashes are structurally distinct because `get_concat_resource` for `L1_DATA_GAS` with zero bounds still encodes the 7-byte resource name prefix, making the felt non-zero. [8](#0-7) 

---

### Impact Explanation

Any validator that receives a proposal containing a V3 invoke transaction with `AllResourceBounds { l2_gas: 0, l1_data_gas: 0 }` will fail to deserialize it and will mark the entire proposal as `Failed`. Because every validator follows the same protobuf path, the proposal is universally rejected. This matches:

> **High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

The secondary hash-domain split also means that if the conversion path were patched to succeed, the stored hash H₁ and any re-derived hash H₂ would disagree, producing a wrong receipt/state value.

---

### Likelihood Explanation

The gateway imposes no lower bound on `l2_gas` or `l1_data_gas` for `AllResourceBounds` transactions. A user can legitimately (or deliberately) submit a V3 invoke with both fields set to zero. The proposer will include it, and every validator will reject the proposal. The trigger requires only a single submitted transaction and no privileged access.

---

### Recommendation

1. **Fix the protobuf deserializer**: Remove the `L1Gas` downgrade heuristic. A transaction that was serialized as `AllResources` must be deserialized as `AllResources` regardless of whether the zero-valued fields are present. The comment `// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2` already signals this is a temporary shim; the downgrade logic should be removed or gated behind an explicit version flag. [9](#0-8) 

2. **Add a round-trip invariant test**: Assert that `RpcInvokeTransactionV3 → protobuf → RpcInvokeTransactionV3` is identity for all `AllResourceBounds` inputs, including the zero-bounds case.

3. **Canonicalize at the hash boundary**: `get_tip_resource_bounds_hash` should either always include `L1_DATA_GAS` for V3 transactions or gate inclusion on a Starknet version constant, not on the runtime variant of `ValidResourceBounds`. [1](#0-0) 

---

### Proof of Concept

```
1. Submit via RPC:
   RpcInvokeTransactionV3 {
       resource_bounds: AllResourceBounds {
           l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
           l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
           l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
       },
       ...
   }

2. Gateway accepts; hash H₁ computed with L1_DATA_GAS felt included.

3. Proposer includes tx in proposal; broadcasts via protobuf.
   Wire bytes: l2_gas = 0, l1_data_gas = 0.

4. Validator deserializes:
   TryFrom<protobuf::ResourceBounds> for ValidResourceBounds
   → l1_data_gas.is_zero() && l2_gas.is_zero() == true
   → returns ValidResourceBounds::L1Gas(l1_gas)   ← wrong variant

5. TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3
   → match value.resource_bounds { ValidResourceBounds::AllResources(b) => b, _ => Err(...) }
   → returns Err(DEPRECATED_RESOURCE_BOUNDS_ERROR)

6. validate_proposal.rs collect::<Result<Vec<_>, _>>() → Err(e)
   → HandledProposalPart::Failed("Failed to convert transactions...")

7. Proposal rejected by all validators; consensus round fails.
```

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L196-210)
```rust
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L586-598)
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
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L129-131)
```rust
        // This conversion can fail only if the resource_bounds are not AllResources.
        Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L426-436)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L609-616)
```rust
            let conversion_results = match conversion_results {
                Ok(results) => results,
                Err(e) => {
                    return HandledProposalPart::Failed(format!(
                        "Failed to convert transactions. Stopping the build of the current \
                         proposal. {e:?}"
                    ));
                }
```

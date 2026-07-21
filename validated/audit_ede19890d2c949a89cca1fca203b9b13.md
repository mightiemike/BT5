### Title
`ValidResourceBounds` collapses from `AllResources` to `L1Gas` on protobuf round-trip, causing consensus block rejection — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` uses value-based type inference to decide which variant to reconstruct. When both `l2_gas` and `l1_data_gas` are zero it always produces `ValidResourceBounds::L1Gas`, even when the original transaction was submitted as `AllResources`. A valid V3 invoke transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is accepted by the gateway and assigned a hash computed under the `AllResources` path (which includes the `L1_DATA_GAS` field in the Poseidon preimage). When the block proposal is propagated to validators via the consensus P2P layer the protobuf round-trip silently changes the variant to `L1Gas`, causing the `RpcInvokeTransactionV3` conversion to fail with `DEPRECATED_RESOURCE_BOUNDS_ERROR`. Every validator rejects the block, breaking consensus for that round.

### Finding Description

**Step 1 – Gateway accepts the transaction.**

`RpcInvokeTransactionV3.resource_bounds` is typed as `AllResourceBounds` (not `ValidResourceBounds`), so any combination of zero/non-zero values is structurally valid. The stateless validator only checks that `max_possible_fee > 0`:

```rust
if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0) {
    return Err(ZeroResourceBounds { resource_bounds });
}
``` [1](#0-0) 

A transaction with `l1_gas = { amount: 1, price: 1 }`, `l2_gas = { 0, 0 }`, `l1_data_gas = { 0, 0 }` passes this check.

**Step 2 – Hash is computed under the `AllResources` path.**

`InternalRpcInvokeTransactionV3` stores `resource_bounds: AllResourceBounds`. When converted to `InvokeTransactionV3` for hashing it is wrapped as `ValidResourceBounds::AllResources(...)`:

```rust
impl From<InternalRpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: InternalRpcInvokeTransactionV3) -> Self {
        Self {
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            ...
        }
    }
}
``` [2](#0-1) 

`get_tip_resource_bounds_hash` then includes the `L1_DATA_GAS` field in the Poseidon preimage (3-element hash):

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2-element hash
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3-element hash
    }
});
``` [3](#0-2) 

**Step 3 – Protobuf serialization preserves all three fields.**

`From<ValidResourceBounds> for protobuf::ResourceBounds` always emits all three fields, even when they are zero:

```rust
ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
    protobuf::ResourceBounds {
        l1_gas: Some(l1_gas.into()),
        l2_gas: Some(l2_gas.into()),
        l1_data_gas: Some(l1_data_gas.into()),   // zero, but present
    },
``` [4](#0-3) 

**Step 4 – Protobuf deserialization silently changes the variant.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` uses value-based inference: if both `l2_gas` and `l1_data_gas` are zero it produces `L1Gas`, discarding the original `AllResources` intent:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant changed
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [5](#0-4) 

**Step 5 – Conversion to `RpcInvokeTransactionV3` fails.**

`RpcInvokeTransactionV3` requires `AllResources`. The conversion from `InvokeTransactionV3` (which now carries `L1Gas`) is explicitly documented to fail in this case:

```rust
// This conversion can fail only if the resource_bounds are not AllResources.
Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
``` [6](#0-5) 

Every validator that receives the consensus block proposal will hit this error and reject the block.

### Impact Explanation

A single unprivileged user can submit a valid V3 invoke transaction (accepted by the gateway, assigned a canonical hash, included in a block proposal) that every validator will reject during consensus deserialization. This breaks the consensus round for that block. The proposer's block is discarded, the round times out, and the network must re-propose. Repeated submissions stall block production. This matches **High – Transaction conversion or signature/hash logic binds the wrong type or executable payload**, and also **High – Mempool/gateway/RPC admission accepts a transaction that is invalid for sequencing**.

### Likelihood Explanation

The trigger requires only a standard V3 invoke with `l2_gas = 0` and `l1_data_gas = 0`, which is structurally valid and passes all gateway checks. No privileged access, no special tooling, and no knowledge of internal state is required. Any user who can submit transactions can trigger this.

### Recommendation

Replace value-based type inference in the protobuf deserializer with an explicit discriminator. The simplest fix is to always deserialize into `AllResources` when all three fields are present in the wire message, reserving `L1Gas` only for messages that omit `l1_data_gas` entirely (the legacy 0.13.2 case noted in the TODO comment). Alternatively, add a boolean tag field to `protobuf::ResourceBounds` that records which variant was originally used.

### Proof of Concept

1. Construct a V3 invoke transaction: `resource_bounds = AllResourceBounds { l1_gas: { max_amount: 1, max_price_per_unit: 1 }, l2_gas: { 0, 0 }, l1_data_gas: { 0, 0 } }`.
2. Submit to the gateway. It passes `validate_resource_bounds` (fee > 0) and is assigned hash H computed under the 3-element `AllResources` Poseidon path.
3. The transaction enters the mempool and is selected by the batcher.
4. The proposer serializes the block proposal as a `ConsensusTransaction` containing `protobuf::InvokeV3` with `resource_bounds = { l1_gas: (1,1), l2_gas: (0,0), l1_data_gas: (0,0) }`.
5. Each validator calls `TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction`, which calls `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. Because `l2_gas.is_zero() && l1_data_gas.is_zero()`, the result is `ValidResourceBounds::L1Gas`.
6. The subsequent `try_into()` to `RpcInvokeTransactionV3` returns `DEPRECATED_RESOURCE_BOUNDS_ERROR`.
7. The validator returns an error from `convert_consensus_tx_to_internal_consensus_tx`, rejects the block, and the consensus round fails.

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L64-68)
```rust
        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L679-694)
```rust
impl From<InternalRpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: InternalRpcInvokeTransactionV3) -> Self {
        Self {
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            tip: tx.tip,
            signature: tx.signature,
            nonce: tx.nonce,
            sender_address: tx.sender_address,
            calldata: tx.calldata,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
            proof_facts: tx.proof_facts,
        }
    }
```

**File:** crates/starknet_api/src/transaction_hash.rs (L202-210)
```rust
    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L431-435)
```rust
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L479-488)
```rust
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
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L129-131)
```rust
        // This conversion can fail only if the resource_bounds are not AllResources.
        Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
    }
```

### Title
Protobuf `ResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Causing Consensus Proposal Rejection for Valid V3 Invoke Transactions - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converts any `AllResources` whose `l2_gas` and `l1_data_gas` are both zero into `ValidResourceBounds::L1Gas`. A subsequent conversion step (`TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3`) hard-rejects any non-`AllResources` variant. Together, a valid V3 invoke transaction submitted with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is accepted by the gateway and included in a block by the proposer, but when the same transaction is received by a validator over P2P consensus (as a protobuf `InvokeV3WithProof`), the deserialization silently changes the resource-bounds variant, the inner conversion fails with `DEPRECATED_RESOURCE_BOUNDS_ERROR`, and the entire proposal is rejected.

### Finding Description

**Step 1 – Protobuf deserialization silently changes the variant.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` (lines 417–437):

```rust
let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // None → zero
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)                      // ← variant changed
} else {
    ValidResourceBounds::AllResources(...)
})
``` [1](#0-0) 

The intent is backward-compatibility with pre-0.13.3 transactions that never carried `l1_data_gas`. However, the check is purely value-based: it cannot distinguish a missing field (old format) from an explicitly-zero field (new format). Any post-0.13.3 transaction with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is silently re-typed to `L1Gas(X)`.

**Step 2 – The downstream conversion hard-rejects `L1Gas`.**

`TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` in `crates/starknet_api/src/rpc_transaction.rs` (lines 586–611):

```rust
resource_bounds: match value.resource_bounds {
    ValidResourceBounds::AllResources(bounds) => bounds,
    _ => {
        return Err(StarknetApiError::OutOfRange { string: "resource_bounds".to_string() });
    }
},
``` [2](#0-1) 

**Step 3 – The consensus conversion path chains both steps.**

`TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` in `crates/apollo_protobuf/src/converters/rpc_transaction.rs` (lines 115–131):

```rust
let snapi_invoke: InvokeTransactionV3 = value.invoke...try_into()?;  // Step 1 runs here
// This conversion can fail only if the resource_bounds are not AllResources.
Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
``` [3](#0-2) 

The comment is incorrect: the conversion *can* fail for a valid `AllResources` transaction whose `l2_gas` and `l1_data_gas` happen to be zero, because Step 1 already changed the variant before Step 2 runs.

**Step 4 – Proposal is rejected.**

The consensus orchestrator in `crates/apollo_consensus_orchestrator/src/validate_proposal.rs` (lines 602–616) collects conversion results and returns `HandledProposalPart::Failed` on any error:

```rust
let conversion_results = match conversion_results {
    Ok(results) => results,
    Err(e) => {
        return HandledProposalPart::Failed(format!(
            "Failed to convert transactions. Stopping the build of the current proposal. {e:?}"
        ));
    }
};
``` [4](#0-3) 

**The invariant broken (analog of the external bug):**

The external Booster bug: `_amount` is deposited but `bal` (a different value derived from intermediate state) is used for minting. Here: the proposer uses the original `AllResources` representation to include the transaction, but the validator uses the protobuf-deserialized `L1Gas` representation — the two representations diverge at the conversion boundary, causing the validator to reject what the proposer accepted.

### Impact Explanation

A validator receiving a consensus proposal that contains a valid V3 invoke transaction with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` will fail to deserialize it from protobuf and will mark the entire proposal as `Failed`. This breaks consensus for that round. The proposer's block is rejected despite being valid. This matches:

> **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**
> **High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

### Likelihood Explanation

Any user can submit a V3 invoke transaction with zero `l2_gas` and `l1_data_gas` (both are optional resource bounds that default to zero). The gateway accepts such transactions — `RpcInvokeTransactionV3` uses `AllResourceBounds` and imposes no minimum on individual resource bounds. The trigger is therefore unprivileged and requires only a standard RPC call. The condition (`l2_gas = 0 AND l1_data_gas = 0`) is common in practice for transactions that do not consume L2 gas or L1 data gas.

### Recommendation

In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, distinguish between a *missing* `l1_data_gas` field (old pre-0.13.3 format → `L1Gas`) and an *explicitly-zero* `l1_data_gas` field (new format → `AllResources`). The `Option` is already available before `unwrap_or_default()`:

```rust
let is_legacy = value.l1_data_gas.is_none();
let l1_data_gas: ResourceBounds = value.l1_data_gas.unwrap_or_default().try_into()?;
Ok(if is_legacy && l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

Additionally, remove or correct the misleading comment in `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` that claims the conversion can only fail for non-`AllResources` bounds. [5](#0-4) 

### Proof of Concept

1. Submit a V3 invoke transaction via RPC with `resource_bounds = AllResources { l1_gas: 1000, l2_gas: 0, l1_data_gas: 0 }`.
2. The gateway accepts it; `convert_rpc_tx_to_internal` computes the hash using `AllResources` (4-element resource-bounds hash including `L1_DATA_GAS = 0`). [6](#0-5) 
3. The proposer includes the transaction in a block and serializes it to `protobuf::InvokeV3WithProof` with `resource_bounds.l1_data_gas = Some(ResourceLimits { max_amount: 0, max_price_per_unit: 0 })`.
4. The validator receives the protobuf message. `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` sees `l1_data_gas.is_zero() && l2_gas.is_zero()` → produces `ValidResourceBounds::L1Gas(l1_gas)`. [7](#0-6) 
5. `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` receives `L1Gas` → returns `Err(OutOfRange)`. [8](#0-7) 
6. `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` maps the error to `DEPRECATED_RESOURCE_BOUNDS_ERROR`. [9](#0-8) 
7. The consensus orchestrator receives the conversion error and returns `HandledProposalPart::Failed`, rejecting the proposer's block. [4](#0-3)

### Citations

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L591-598)
```rust
            resource_bounds: match value.resource_bounds {
                ValidResourceBounds::AllResources(bounds) => bounds,
                _ => {
                    return Err(StarknetApiError::OutOfRange {
                        string: "resource_bounds".to_string(),
                    });
                }
            },
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L122-131)
```rust
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

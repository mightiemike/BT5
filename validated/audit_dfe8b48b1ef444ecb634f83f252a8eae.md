The question describes a real, concrete serialization round-trip bug. Here is the full analysis:

---

### Title
Protobuf Round-Trip Corrupts `ValidResourceBounds` Variant for `AllResources` with Zero L2/L1DataGas, Causing Receiving Gateway to Reject Valid Transactions — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

A transaction with `AllResourceBounds { l1_gas: X, l2_gas: zero, l1_data_gas: zero }` is accepted by the originating gateway but rejected by every receiving node after P2P propagation, because the protobuf round-trip silently changes the `ValidResourceBounds` variant from `AllResources` to `L1Gas`.

### Finding Description

**Serialization side** (`From<ValidResourceBounds> for protobuf::ResourceBounds`):

For the `AllResources` variant, all three fields are emitted as `Some(...)`, even when zero: [1](#0-0) 

So `AllResourceBounds { l1_gas: X, l2_gas: zero, l1_data_gas: zero }` serializes to `protobuf::ResourceBounds { l1_gas: Some(X), l2_gas: Some(zero), l1_data_gas: Some(zero) }`.

**Deserialization side** (`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`):

The deserializer uses a value-based heuristic to reconstruct the variant: [2](#0-1) 

Because both `l2_gas` and `l1_data_gas` are zero, the condition on line 431 is true and the result is `ValidResourceBounds::L1Gas(l1_gas)` — not `AllResources`. The original variant is lost.

**Failure at conversion to `RpcInvokeTransactionV3`:**

The mempool P2P path uses `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3`: [3](#0-2) 

This calls `snapi_invoke.try_into()` which is `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3`: [4](#0-3) 

Since the variant is now `L1Gas`, the `_` arm fires and returns `StarknetApiError::OutOfRange`, which is mapped to `DEPRECATED_RESOURCE_BOUNDS_ERROR`: [5](#0-4) 

**The originating gateway accepts the transaction** because `RpcInvokeTransactionV3.resource_bounds` is typed as `AllResourceBounds` (not `ValidResourceBounds`), and the stateless validator explicitly allows zero l2_gas/l1_data_gas: [6](#0-5) 

The stateless validator test confirms `AllResourceBounds { l1_gas: NON_EMPTY_RESOURCE_BOUNDS, ..Default::default() }` (zero l2/l1_data) is valid.

**The full call path is confirmed:**

`MempoolP2pPropagator::broadcast_queued_transactions` → `RpcTransactionBatch` → `protobuf::MempoolTransactionBatch` → bytes → `MempoolP2pRunner` → `gateway_client.add_tx` → `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` → `DEPRECATED_RESOURCE_BOUNDS_ERROR`. [7](#0-6) 

### Impact Explanation

Any user who submits a valid `InvokeV3` transaction with `l2_gas = 0` and `l1_data_gas = 0` (a legitimate pre-0.13.3-style transaction) will have it accepted by the originating node but silently dropped by all receiving nodes. The transaction will never propagate across the network, effectively making it unsequenceable unless the originating node is the proposer. This matches the **High** impact category: "Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."

### Likelihood Explanation

Any user submitting a V3 invoke with only L1 gas set (a common pattern for pre-0.13.3 transactions) triggers this. No special privileges are required — only a standard RPC `add_transaction` call.

### Recommendation

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` deserializer must not use a value-based heuristic to reconstruct the variant. Options:
1. Add an explicit discriminant field to `protobuf::ResourceBounds` (e.g., a boolean `is_all_resources`) to preserve the original variant.
2. Always deserialize as `AllResources` when all three fields are present (even if zero), reserving `L1Gas` only for the legacy case where `l1_data_gas` is `None`.

Option 2 is the minimal fix: change line 431 to only produce `L1Gas` when `l1_data_gas` was absent (`None` before `unwrap_or_default`), not when it is present but zero. [8](#0-7) 

### Proof of Concept

```rust
// Construct a valid RpcInvokeTransactionV3 with zero l2_gas and l1_data_gas
let tx = RpcInvokeTransactionV3 {
    resource_bounds: AllResourceBounds {
        l1_gas: ResourceBounds { max_amount: GasAmount(100), max_price_per_unit: GasPrice(1) },
        l2_gas: ResourceBounds::default(),      // zero
        l1_data_gas: ResourceBounds::default(), // zero
    },
    // ... other fields
};
let rpc_tx = RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx.clone()));

// Serialize: RpcTransactionBatch → bytes
let batch = RpcTransactionBatch(vec![rpc_tx]);
let bytes = Vec::<u8>::from(batch);

// Deserialize: bytes → RpcTransactionBatch
// This will return Err(DEPRECATED_RESOURCE_BOUNDS_ERROR) instead of Ok(tx)
let result = RpcTransactionBatch::try_from(bytes);
assert!(result.is_err()); // Bug: should be Ok and equal to original
```

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L417-437)
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
}
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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L29-30)
```rust
const DEPRECATED_RESOURCE_BOUNDS_ERROR: ProtobufConversionError =
    ProtobufConversionError::MissingField { field_description: "ResourceBounds::l1_data_gas" };
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L550-566)
```rust
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, SizeOf)]
pub struct RpcInvokeTransactionV3 {
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub resource_bounds: AllResourceBounds,
    pub tip: Tip,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
    #[serde(default, skip_serializing_if = "Proof::is_empty")]
    pub proof: Proof,
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

**File:** crates/apollo_mempool_p2p/src/runner/mod.rs (L104-128)
```rust
                Some((message_result, broadcasted_message_metadata)) = self.broadcasted_topic_server.next() => {
                    match message_result {
                        Ok(message) => {
                            // TODO(alonl): consider calculating the tx_hash and printing it instead of the entire tx.
                            debug!("Received transaction batch from network, forwarding to gateway. Batch: {:?}", message.0);
                            for rpc_tx in message.0 {
                                let permit = match gateway_semaphore.clone().try_acquire_owned() {
                                    Ok(permit) => permit,
                                    Err(_) => {
                                        warn!(
                                            "Rejecting transaction due to backpressure. \
                                             Transaction: {rpc_tx:?}"
                                        );
                                        continue;
                                    }
                                };
                                let gateway_client = self.gateway_client.clone();
                                let message_metadata = Some(broadcasted_message_metadata.clone());
                                gateway_futures.push(async move {
                                    let _permit = permit;
                                    gateway_client.add_tx(
                                        GatewayInput { rpc_tx, message_metadata }
                                    ).await
                                });
                            }
```

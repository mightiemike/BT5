### Title
Protobuf `ValidResourceBounds` Deserialization Heuristic Silently Downgrades `AllResources` to `L1Gas`, Causing Consensus P2P Rejection of Valid Transactions - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf-to-Rust conversion for `ValidResourceBounds` uses a zero-value heuristic to decide whether a transaction carries `L1Gas` (pre-0.13.3) or `AllResources` (0.13.3+) resource bounds. When an `AllResources` transaction has both `l2_gas` and `l1_data_gas` set to zero, the deserializer silently downgrades it to `L1Gas`. The downstream `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` conversion then hard-rejects any non-`AllResources` variant, causing the consensus P2P receiver to drop the transaction with `DEPRECATED_RESOURCE_BOUNDS_ERROR`. A valid transaction accepted at the gateway is therefore permanently blocked from being sequenced by any peer node.

### Finding Description

**Step 1 — Gateway accepts the transaction as `AllResources`.**

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds` (not `ValidResourceBounds`). A transaction with `l1_gas > 0`, `l2_gas = 0`, `l1_data_gas = 0` passes all gateway stateless checks and is stored internally as `InternalRpcInvokeTransactionV3` with `resource_bounds: AllResourceBounds`. The transaction hash is computed via `get_invoke_transaction_v3_hash`, which includes the `l1_data_gas` felt in the Poseidon preimage because the variant is `AllResources`. [1](#0-0) [2](#0-1) 

**Step 2 — Serialization to protobuf preserves all three resource fields.**

`From<ValidResourceBounds> for protobuf::ResourceBounds` always emits all three fields (`l1_gas`, `l2_gas`, `l1_data_gas`). For an `AllResources` transaction with zero `l2_gas`/`l1_data_gas`, the wire message carries `l2_gas = {0, 0}` and `l1_data_gas = {0, 0}`. [3](#0-2) 

**Step 3 — Deserialization applies a zero-value heuristic and produces `L1Gas`.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` checks `l1_data_gas.is_zero() && l2_gas.is_zero()`. When both are zero it returns `ValidResourceBounds::L1Gas(l1_gas)`, discarding the `AllResources` semantics entirely. [4](#0-3) 

This converter is invoked inside `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3`: [5](#0-4) 

**Step 4 — `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` hard-rejects `L1Gas`.**

The consensus P2P path converts `protobuf::InvokeV3WithProof → InvokeTransactionV3 → RpcInvokeTransactionV3`. The second conversion explicitly rejects any variant that is not `AllResources`: [6](#0-5) 

The caller maps this error to `DEPRECATED_RESOURCE_BOUNDS_ERROR`: [7](#0-6) 

**Step 5 — Consensus deserialization fails; the block proposal is rejected.**

`TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction` propagates the error upward, causing the receiving node to reject the entire consensus message containing the transaction. [8](#0-7) 

**Contrast with the correct `AllResourceBounds` converter.**

`rpc_transaction.rs` defines a separate `TryFrom<protobuf::ResourceBounds> for AllResourceBounds` that does **not** apply the zero-value heuristic and always produces `AllResourceBounds`. The consensus path uses the wrong converter. [9](#0-8) 

### Impact Explanation

Any `AllResources` Invoke V3 transaction where both `l2_gas.max_amount == 0 && l2_gas.max_price_per_unit == 0` and `l1_data_gas.max_amount == 0 && l1_data_gas.max_price_per_unit == 0` (but `l1_gas > 0`) is accepted at the gateway, enters the mempool, and is included in a block proposal by the originating node's batcher. When that proposal is broadcast over the consensus P2P wire, every peer node fails to deserialize it and rejects the proposal. The originating node cannot reach consensus on any block containing such a transaction.

Matching impact scope: **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

### Likelihood Explanation

The condition is reachable by any user who submits a V3 Invoke transaction with non-zero `l1_gas` and zero `l2_gas`/`l1_data_gas`. The gateway's `validate_resource_bounds` check only requires *at least one* resource bound to be non-zero, so `l1_gas > 0` with the other two at zero passes validation. No special privilege is required.

### Recommendation

In `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` (and the `DeclareV3Common` equivalent), replace the call to `ValidResourceBounds::try_from(...)` with `AllResourceBounds::try_from(...)` followed by `ValidResourceBounds::AllResources(...)`. The zero-value heuristic in `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` is only appropriate for the historical P2P sync path (pre-0.13.3 transactions); it must not be used for the consensus path, which exclusively handles V3 `AllResources` transactions.

### Proof of Concept

```
1. Submit an Invoke V3 transaction to the gateway with:
     resource_bounds = AllResourceBounds {
         l1_gas:      { max_amount: 1_000_000, max_price_per_unit: 1 },
         l2_gas:      { max_amount: 0,         max_price_per_unit: 0 },
         l1_data_gas: { max_amount: 0,         max_price_per_unit: 0 },
     }

2. Gateway accepts it; hash is computed with AllResources (3-felt preimage).

3. Batcher includes it in a block proposal; proposal is serialized to
   protobuf::ConsensusTransaction::InvokeV3(InvokeV3WithProof { ... }).

4. Peer node receives the protobuf message and calls:
     TryFrom<protobuf::ResourceBounds> for ValidResourceBounds
   → l2_gas.is_zero() && l1_data_gas.is_zero() == true
   → returns ValidResourceBounds::L1Gas(l1_gas)   ← wrong variant

5. TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3 matches:
     ValidResourceBounds::AllResources(bounds) => bounds,
     _ => return Err(OutOfRange { "resource_bounds" })   ← triggers here

6. map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR) fires.
   ConsensusTransaction deserialization returns Err.
   Peer rejects the block proposal.
   Consensus cannot finalize any block containing this transaction.
``` [10](#0-9) [11](#0-10)

### Citations

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L471-489)
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
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L593-598)
```rust
impl TryFrom<protobuf::InvokeV3> for InvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::InvokeV3) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L1027-1052)
```rust
impl TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ConsensusTransaction) -> Result<Self, Self::Error> {
        let txn = value.txn.ok_or(missing("ConsensusTransaction::txn"))?;
        let txn = match txn {
            protobuf::consensus_transaction::Txn::DeclareV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::Declare(
                    RpcDeclareTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::DeployAccountV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::DeployAccount(
                    RpcDeployAccountTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::InvokeV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::Invoke(
                    RpcInvokeTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::L1Handler(txn) => {
                ConsensusTransaction::L1Handler(txn.try_into()?)
            }
        };
        Ok(txn)
    }
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L29-30)
```rust
const DEPRECATED_RESOURCE_BOUNDS_ERROR: ProtobufConversionError =
    ProtobufConversionError::MissingField { field_description: "ResourceBounds::l1_data_gas" };
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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L212-224)
```rust
impl TryFrom<protobuf::ResourceBounds> for AllResourceBounds {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        Ok(Self {
            l1_gas: value.l1_gas.ok_or(missing("ResourceBounds::l1_gas"))?.try_into()?,
            l2_gas: value.l2_gas.ok_or(missing("ResourceBounds::l2_gas"))?.try_into()?,
            l1_data_gas: value
                .l1_data_gas
                .ok_or(missing("ResourceBounds::l1_data_gas"))?
                .try_into()?,
        })
    }
}
```

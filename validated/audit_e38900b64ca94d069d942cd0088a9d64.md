### Title
`ValidResourceBounds` Protobuf Round-Trip Silently Collapses `AllResources` to `L1Gas`, Producing a Different Transaction Hash Preimage — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a value-based zero-check heuristic to decide which enum variant to reconstruct. Because `get_tip_resource_bounds_hash` produces structurally different hash preimages for `L1Gas` (2 resource felts) versus `AllResources` (3 resource felts), any `AllResources` transaction whose `l2_gas` and `l1_data_gas` fields are both zero will silently deserialize as `L1Gas`, yielding a different transaction hash than the one the signer committed to.

### Finding Description

**Serialization path** (`From<ValidResourceBounds> for protobuf::ResourceBounds`): [1](#0-0) 

When `ValidResourceBounds::L1Gas` is serialized, `l1_data_gas` is explicitly written as `ResourceBounds::default()` (amount=0, price=0). When `ValidResourceBounds::AllResources` with zero `l2_gas` and zero `l1_data_gas` is serialized, the wire bytes are **identical** to those of an `L1Gas` transaction.

**Deserialization path** (`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`): [2](#0-1) 

The reconstructed variant is chosen purely by whether the decoded `l2_gas` and `l1_data_gas` are zero. There is no explicit type tag. An `AllResources` transaction with both fields zero is therefore reconstructed as `L1Gas`.

**Hash divergence** (`get_tip_resource_bounds_hash`): [3](#0-2) 

- `L1Gas` → hash chain over `[tip, concat(L1_GAS, …), concat(L2_GAS, 0)]` — **2 resource felts**
- `AllResources` → hash chain over `[tip, concat(L1_GAS, …), concat(L2_GAS, 0), concat(L1_DATA_GAS, 0)]` — **3 resource felts**

Even when the numeric values are identical, the Poseidon hash output differs because the input length differs. The transaction hash that the account signed (over the `AllResources` preimage) will not match the hash recomputed from the deserialized `L1Gas` representation.

The codebase itself acknowledges this invariant break in the consensus round-trip test: [4](#0-3) 

The test works around it by forcing a non-zero `l2_gas.max_amount`, but the underlying production converter is unchanged.

The `AllResourceBounds` deserializer used for RPC transactions does **not** have this bug — it always returns `AllResourceBounds`: [5](#0-4) 

The vulnerable `ValidResourceBounds` path is used for the non-RPC `DeployAccountTransactionV3` and `InvokeTransactionV3` types that travel over P2P sync and are served back through RPC.

The `InternalRpcInvokeTransactionV3` and `InternalRpcDeclareTransactionV3` both fix `resource_bounds` as `AllResourceBounds`, so the gateway-to-batcher path is unaffected: [6](#0-5) 

### Impact Explanation

A syncing node that receives a committed block containing a `ValidResourceBounds::AllResources` transaction with zero `l2_gas` and `l1_data_gas` will:

1. Deserialize it as `ValidResourceBounds::L1Gas`.
2. Recompute the transaction hash using the 2-felt preimage.
3. Obtain a hash that differs from the one committed in the block header.

Depending on whether the sync layer re-verifies the hash post-deserialization, the outcome is either:
- **Block/transaction rejection** — the syncing node cannot advance its chain tip (liveness failure).
- **Silent wrong state** — the node stores the transaction under the wrong hash and subsequently serves incorrect data via `starknet_getTransactionByHash`, `starknet_estimateFee`, or `starknet_simulateTransactions`, all of which would use the wrong `GasVectorComputationMode` (`NoL2Gas` instead of `All`), producing authoritative-looking but incorrect fee and gas values.

This matches: **High — Transaction conversion or signature/hash logic binds the wrong hash or executable payload** and **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value**.

### Likelihood Explanation

The trigger condition — `AllResources` with both `l2_gas` and `l1_data_gas` equal to zero — is reachable whenever a sequencer emits a transaction that uses the `AllResources` variant for forward-compatibility reasons but sets those two bounds to zero (e.g., an operator transaction that only pays L1 gas). The `AllResourceBounds::create_for_testing()` helper already produces exactly this shape. No privileged access is required; any node that syncs such a block is affected.

### Recommendation

Replace the value-based heuristic with an explicit type tag in the protobuf schema, or add a dedicated boolean/enum field `resource_bounds_type` to `ResourceBounds`. On the deserialization side, use that tag rather than inspecting numeric values. Until the schema is updated, the deserializer should default to `AllResources` whenever `l1_data_gas` is present in the wire message (even if zero), reserving `L1Gas` only for messages where `l1_data_gas` is absent (`None`), which is the pre-0.13.3 wire format:

```rust
Ok(match value.l1_data_gas {
    None => ValidResourceBounds::L1Gas(l1_gas),          // legacy 0.13.2 wire
    Some(_) => ValidResourceBounds::AllResources(        // all modern messages
        AllResourceBounds { l1_gas, l2_gas, l1_data_gas }
    ),
})
```

This preserves backward compatibility with 0.13.2 peers (which never send `l1_data_gas`) while correctly reconstructing `AllResources` for all modern messages, regardless of whether the bounds are zero.

### Proof of Concept

1. Construct a `ValidResourceBounds::AllResources` with `l1_gas = ResourceBounds { max_amount: 100, max_price_per_unit: 1 }`, `l2_gas = ResourceBounds::default()`, `l1_data_gas = ResourceBounds::default()`.
2. Serialize via `From<ValidResourceBounds> for protobuf::ResourceBounds` — wire bytes are identical to a `L1Gas(l1_gas)` message because both `l2_gas` and `l1_data_gas` are written as `ResourceBounds::default()`.
3. Deserialize via `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` — returns `ValidResourceBounds::L1Gas(l1_gas)` because `l1_data_gas.is_zero() && l2_gas.is_zero()`.
4. Call `get_tip_resource_bounds_hash` on both the original and the deserialized value with the same `Tip`:
   - Original (`AllResources`): Poseidon hash of `[tip, concat(L1_GAS, 100, 1), concat(L2_GAS, 0, 0), concat(L1_DATA_GAS, 0, 0)]`
   - Deserialized (`L1Gas`): Poseidon hash of `[tip, concat(L1_GAS, 100, 1), concat(L2_GAS, 0, 0)]`
   - The two hashes differ.
5. The full transaction hash (used in `calculate_transaction_hash`) embeds `tip_resource_bounds_hash` as a chained field, so the final `TransactionHash` values diverge, breaking signature verification and block commitment checks for any

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

**File:** crates/apollo_protobuf/src/converters/consensus_test.rs (L26-47)
```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(transactions: &mut [ConsensusTransaction]) {
    let transaction = &mut transactions[0];
    match transaction {
        ConsensusTransaction::RpcTransaction(rpc_transaction) => match rpc_transaction {
            RpcTransaction::Declare(RpcDeclareTransaction::V3(RpcDeclareTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::Invoke(RpcInvokeTransaction::V3(RpcInvokeTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(
                RpcDeployAccountTransactionV3 { resource_bounds, .. },
            )) => {
                resource_bounds.l2_gas.max_amount = GasAmount(1);
            }
        },
        ConsensusTransaction::L1Handler(_) => {}
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L615-628)
```rust
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, SizeOf)]
pub struct InternalRpcInvokeTransactionV3 {
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
    pub proof_facts: ProofFacts,
}
```

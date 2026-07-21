### Title
`ValidResourceBounds` Variant Lost in Protobuf Round-Trip Causes Consensus Hash Divergence and Peer Deserialization Failure - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` silently collapses an `AllResources` variant (with zero `l2_gas` and `l1_data_gas`) into the `L1Gas` variant. Because the hash function `get_tip_resource_bounds_hash` produces structurally different output for these two variants, a transaction accepted by the gateway with `AllResourceBounds { l2_gas: 0, l1_data_gas: 0 }` will either (a) cause consensus peers to fail deserialization entirely, or (b) cause peers to recompute a different transaction hash than the proposer stored — a direct type/hash binding divergence across the public-to-internal conversion boundary.

### Finding Description

**Root cause — lossy `ValidResourceBounds` deserialization:** [1](#0-0) 

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant changed
} else {
    ValidResourceBounds::AllResources(...)
})
```

The protobuf wire format carries no discriminant between `L1Gas` and `AllResources`; the deserializer infers the variant from whether the numeric values are zero. An `AllResources` payload with `l2_gas = 0` and `l1_data_gas = 0` is indistinguishable on the wire from a `L1Gas` payload, so it is silently re-typed to `L1Gas` on deserialization.

**Serialization path (proposer side):**

`RpcInvokeTransactionV3` stores `AllResourceBounds` directly. [2](#0-1) 

When the gateway converts it to `InternalRpcInvokeTransactionV3`, the `InvokeTransactionV3Trait` implementation wraps it as `ValidResourceBounds::AllResources(...)`: [3](#0-2) 

The hash function branches on the variant: [4](#0-3) 

For `AllResources`, `L1_DATA_GAS` is included in the hash preimage (3-element resource array). For `L1Gas`, it is omitted (2-element array). These produce **different Poseidon hashes** even when the numeric values of `l2_gas` and `l1_data_gas` are both zero.

**Deserialization path (peer side):**

Consensus protobuf deserialization calls: [5](#0-4) 

which invokes `ValidResourceBounds::try_from(protobuf::ResourceBounds)` — the lossy converter above — producing `L1Gas`. The subsequent conversion to `RpcInvokeTransactionV3` then fails: [6](#0-5) 

```rust
resource_bounds: match value.resource_bounds {
    ValidResourceBounds::AllResources(bounds) => bounds,
    _ => return Err(StarknetApiError::OutOfRange { ... }),  // ← triggered
},
```

The same failure pattern is explicit for `DeployAccountV3`: [7](#0-6) 

**The two-path divergence:**

| Stage | Variant | Hash preimage elements |
|---|---|---|
| Gateway (proposer) | `AllResources` | `[tip, L1_GAS, L2_GAS(0), L1_DATA_GAS(0)]` |
| Peer after protobuf round-trip | `L1Gas` | `[tip, L1_GAS, L2_GAS(0)]` |

Hash H1 ≠ H2. If deserialization does not fail outright, `convert_rpc_tx_to_internal` recomputes and stores H2: [8](#0-7) 

### Impact Explanation

A user can submit a valid `RpcInvokeTransactionV3` (or `DeployAccountV3`) with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`. The gateway accepts it and computes hash H1. When the proposer broadcasts the block via consensus protobuf, every peer either:

1. **Fails deserialization** (`OutOfRange` / `DEPRECATED_RESOURCE_BOUNDS_ERROR`) and cannot process the block proposal — consensus liveness failure, or
2. **Recomputes a different hash H2** and stores the transaction under a different key than the proposer — state/receipt divergence.

This matches: **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

### Likelihood Explanation

The trigger is an unprivileged user submitting a V3 transaction with zero `l2_gas` and `l1_data_gas` bounds. This is a realistic scenario for wallets that only specify L1 gas (pre-0.13.3 style) but use the V3 transaction format. No special privileges or malicious intent are required; the condition arises naturally from the `AllResourceBounds` type being the only accepted format at the RPC layer.

### Recommendation

1. **Add a discriminant to the protobuf schema** for `ResourceBounds` (e.g., a boolean `is_all_resources` field) so the variant survives serialization without inference from numeric values.
2. **Use `TryFrom<protobuf::ResourceBounds> for AllResourceBounds`** (which requires all three fields and never produces `L1Gas`) for all consensus and mempool transaction paths, since those paths only accept V3 transactions: [9](#0-8) 
3. **Add a round-trip test** for `AllResourceBounds { l2_gas: 0, l1_data_gas: 0 }` through the consensus protobuf path to catch this regression.

### Proof of Concept

```
1. Construct RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      ResourceBounds { max_amount: 100, max_price_per_unit: 1 },
         l2_gas:      ResourceBounds { max_amount: 0,   max_price_per_unit: 0 },
         l1_data_gas: ResourceBounds { max_amount: 0,   max_price_per_unit: 0 },
     }

2. Gateway: convert_rpc_tx_to_internal()
     → InternalRpcInvokeTransactionV3::resource_bounds() returns AllResources(...)
     → get_tip_resource_bounds_hash hashes [tip, L1_GAS, L2_GAS(0), L1_DATA_GAS(0)]
     → tx_hash = H1

3. Serialize to protobuf::InvokeV3:
     resource_bounds = { l1_gas: Some(100/1), l2_gas: Some(0/0), l1_data_gas: Some(0/0) }

4. Peer deserializes protobuf::InvokeV3 → InvokeTransactionV3:
     ValidResourceBounds::try_from(resource_bounds):
       l1_data_gas = Some(0/0).unwrap_or_default() → is_zero() = true
       l2_gas.is_zero() = true
       → returns ValidResourceBounds::L1Gas(100/1)   ← WRONG VARIANT

5a. TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3:
      match ValidResourceBounds::L1Gas(_) → Err(OutOfRange)
      → consensus deserialization FAILS, peer rejects block proposal

5b. (If path bypasses the check) convert_rpc_tx_to_internal recomputes:
      InternalRpcInvokeTransactionV3::resource_bounds() → AllResources(0,0)
      get_tip_resource_bounds_hash hashes [tip, L1_GAS, L2_GAS(0)]  ← 2 elements, not 3
      → tx_hash = H2 ≠ H1
```

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L593-660)
```rust
impl TryFrom<protobuf::InvokeV3> for InvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::InvokeV3) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;

        let tip = Tip(value.tip);

        let signature = TransactionSignature(
            value
                .signature
                .ok_or(missing("InvokeV3::signature"))?
                .parts
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?
                .into(),
        );

        let nonce = Nonce(value.nonce.ok_or(missing("InvokeV3::nonce"))?.try_into()?);

        let sender_address = value.sender.ok_or(missing("InvokeV3::sender"))?.try_into()?;

        let calldata =
            value.calldata.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?;

        let calldata = Calldata(calldata.into());

        let nonce_data_availability_mode =
            enum_int_to_volition_domain(value.nonce_data_availability_mode)?;

        let fee_data_availability_mode =
            enum_int_to_volition_domain(value.fee_data_availability_mode)?;

        let paymaster_data = PaymasterData(
            value.paymaster_data.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?,
        );

        let account_deployment_data = AccountDeploymentData(
            value
                .account_deployment_data
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?,
        );

        let proof_facts: ProofFacts = value
            .proof_facts
            .into_iter()
            .map(Felt::try_from)
            .collect::<Result<Vec<_>, _>>()?
            .into();

        Ok(Self {
            resource_bounds,
            tip,
            signature,
            nonce,
            sender_address,
            calldata,
            nonce_data_availability_mode,
            fee_data_availability_mode,
            paymaster_data,
            account_deployment_data,
            proof_facts,
        })
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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L99-106)
```rust
impl TryFrom<protobuf::DeployAccountV3> for RpcDeployAccountTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::DeployAccountV3) -> Result<Self, Self::Error> {
        let snapi_deploy_account: DeployAccountTransactionV3 = value.try_into()?;
        // This conversion can fail only if the resource_bounds are not AllResources.
        snapi_deploy_account.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)
    }
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

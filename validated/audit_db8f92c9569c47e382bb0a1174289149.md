### Title
Protobuf `ValidResourceBounds` Deserialization Collapses `AllResources{l2_gas=0, l1_data_gas=0}` to `L1Gas`, Breaking P2P Propagation and Transaction Hash Canonicalization — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The gateway accepts an `InvokeV3` transaction whose `resource_bounds` carries `AllResourceBounds { l1_gas=X, l2_gas=0, l1_data_gas=0 }`. When that transaction is serialized to protobuf and deserialized by any peer, the `ValidResourceBounds` converter silently collapses the variant from `AllResources` to `L1Gas`. A downstream conversion then hard-rejects the `L1Gas` variant, so every peer drops the transaction. If the originating node is the block proposer and includes the transaction in a block proposal, every peer rejects the entire block, causing a consensus stall.

---

### Finding Description

**Serialization boundary — `ValidResourceBounds::try_from(protobuf::ResourceBounds)`**

```
// crates/apollo_protobuf/src/converters/transaction.rs  lines 417-436
let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // None → zero
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← collapses AllResources to L1Gas
} else {
    ValidResourceBounds::AllResources(...)
})
```

The protobuf field `l1_data_gas` is declared `optional` in the wire schema. When a sender serializes `AllResources { l1_gas=X, l2_gas=0, l1_data_gas=0 }`, the serializer emits `l1_data_gas = Some(zero)`. The deserializer unwraps it to zero, then the zero-check fires and the variant is downgraded to `L1Gas(X)`.

**Downstream rejection — `RpcInvokeTransactionV3::try_from(InvokeTransactionV3)`**

```
// crates/starknet_api/src/rpc_transaction.rs  lines 586-611
resource_bounds: match value.resource_bounds {
    ValidResourceBounds::AllResources(bounds) => bounds,
    _ => return Err(StarknetApiError::OutOfRange { ... }),  // ← rejects L1Gas
},
```

Every P2P deserialization path for consensus and mempool transactions goes through this conversion. Because the variant is now `L1Gas`, the conversion fails and the transaction is dropped.

**Hash domain divergence**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` (lines 188–211) hashes a different number of resource felts depending on the variant:

- `AllResources`: `poseidon(tip, l1_gas_felt, l2_gas_felt, l1_data_gas_felt)` — 4 elements
- `L1Gas`: `poseidon(tip, l1_gas_felt, l2_gas_felt)` — 3 elements

Even with `l2_gas=0` and `l1_data_gas=0`, the two poseidon outputs are distinct. The gateway stores hash H₁ (AllResources path); any node that somehow reaches the hash step on the deserialized form would compute H₂ ≠ H₁.

---

### Impact Explanation

1. A user submits an `InvokeV3` transaction with `AllResourceBounds { l1_gas=X, l2_gas=0, l1_data_gas=0 }` to the gateway RPC endpoint.
2. The gateway's stateless validator accepts it (zero l2/data gas is explicitly tested as valid in `stateless_transaction_validator_test.rs`).
3. The gateway computes hash H₁ using the `AllResources` path and stores the transaction in the mempool.
4. The transaction is propagated to peers via the P2P mempool or included in a consensus block proposal.
5. Every peer's protobuf deserializer collapses the variant to `L1Gas`, causing `RpcInvokeTransactionV3::try_from` to return `DEPRECATED_RESOURCE_BOUNDS_ERROR`.
6. Peers reject the transaction (mempool propagation failure) or reject the entire block proposal (consensus failure).
7. If the originating node is the block proposer, it repeatedly proposes blocks that all peers reject, stalling consensus.

This matches: **High — Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

---

### Likelihood Explanation

The trigger requires only a standard RPC `starknet_addInvokeTransaction` call with `l2_gas` and `l1_data_gas` set to zero — a configuration that the gateway's stateless validator explicitly permits and that is the natural default for `AllResourceBounds`. No privileged access, no malformed bytes, and no special network position are required. Any external user can trigger this.

---

### Recommendation

1. **Enforce `AllResources` on the wire for all post-0.13.2 transactions.** Remove the `unwrap_or_default()` fallback and the zero-collapse branch in `ValidResourceBounds::try_from(protobuf::ResourceBounds)`. The TODO comment (`// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2`) should be resolved immediately for the mempool and consensus paths.

2. **Add a gateway stateless check** that rejects `AllResourceBounds` where both `l2_gas` and `l1_data_gas` are zero, since such transactions cannot be propagated. Alternatively, canonicalize them to `L1Gas` at ingress so the hash is computed consistently.

3. **Add a round-trip invariant test**: serialize `AllResources { l2_gas=0, l1_data_gas=0 }` to protobuf and assert that deserialization produces `AllResources`, not `L1Gas`.

---

### Proof of Concept

```
# 1. Submit via RPC (gateway accepts it, computes hash H1 with AllResources path)
POST /gateway/add_transaction
{
  "type": "INVOKE",
  "version": "0x3",
  "resource_bounds": {
    "l1_gas":      { "max_amount": "0x1000", "max_price_per_unit": "0x1" },
    "l2_gas":      { "max_amount": "0x0",    "max_price_per_unit": "0x0" },
    "l1_data_gas": { "max_amount": "0x0",    "max_price_per_unit": "0x0" }
  },
  ...
}
# Gateway stores tx with hash H1 (AllResources, 4-element poseidon).

# 2. Observe P2P propagation failure:
#    - Serializer emits AllResources → protobuf ResourceBounds { l1_data_gas: Some(zero) }
#    - Peer deserializer: l1_data_gas.is_zero() && l2_gas.is_zero() → L1Gas
#    - RpcInvokeTransactionV3::try_from(InvokeTransactionV3) → Err(OutOfRange)
#    - Peer logs DEPRECATED_RESOURCE_BOUNDS_ERROR and drops the transaction.

# 3. If originating node is block proposer:
#    - Block proposal includes the transaction serialized via AllResources path.
#    - All peers deserialize → L1Gas → conversion fails → block rejected.
#    - Consensus stalls for the duration the proposer holds the slot.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_protobuf/src/proto/p2p/proto/transaction.proto (L13-19)
```text
message ResourceBounds {
    ResourceLimits l1_gas = 1;
    // This can be None only in transactions that don't support l2 gas.
    // Starting from 0.14.0, MempoolTransaction and ConsensusTransaction shouldn't have None here.
    optional ResourceLimits l1_data_gas = 2;
    ResourceLimits l2_gas = 3;
}
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

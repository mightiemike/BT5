### Title
`ValidResourceBounds::AllResources` with zero `l2_gas`/`l1_data_gas` silently downcasts to `L1Gas` on protobuf deserialization, producing a divergent transaction hash preimage — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` applies a value-based heuristic: if both `l2_gas` and `l1_data_gas` are zero it emits `ValidResourceBounds::L1Gas`, even when the sender originally constructed and signed the transaction as `ValidResourceBounds::AllResources`. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` field in the Poseidon preimage for `AllResources` but omits it for `L1Gas`, the two variants produce structurally different hashes for the same numeric bounds. Any node that receives the transaction over P2P will compute a different transaction hash than the originating node, breaking block-commitment agreement.

---

### Finding Description

**Invariant violated (analog to the external report):**

The external report shows that `totalLiquidityWeight` (a global accumulator) is updated when `_addVaderPair` is called, but `pastLiquidityWeights` (per-item tracking) is not yet populated for the new pair. When `syncVaderPrice` reads the global total it is inflated relative to the per-pair sum, skewing the price.

The sequencer analog: the *transaction hash* (the "global total") is computed from `ValidResourceBounds` (the "per-item type"). The originating node always treats the bounds as `AllResources` (hash includes `L1_DATA_GAS`), while the receiving node, after protobuf round-trip, treats the same bounds as `L1Gas` (hash omits `L1_DATA_GAS`). The two representations of the same numeric data produce different hash preimages — the global hash is inconsistent with the per-field representation.

**Step-by-step trace:**

1. A user submits an `RpcInvokeTransactionV3` with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`. The gateway accepts it (the stateless validator permits any `AllResourceBounds` with at least one non-zero field).

2. The gateway converts it to `InternalRpcInvokeTransactionV3`, which stores `resource_bounds: AllResourceBounds`. Its `InvokeTransactionV3Trait::resource_bounds()` implementation unconditionally returns `ValidResourceBounds::AllResources(self.resource_bounds)`. [1](#0-0) 

3. `calculate_transaction_hash` on the originating node calls `get_invoke_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash` with `ValidResourceBounds::AllResources(...)`. The `AllResources` branch appends `get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)` to the hash chain, so the preimage is `Poseidon(tip, L1_GAS, L2_GAS=0, L1_DATA_GAS=0)` → hash **H₁**. [2](#0-1) 

4. The transaction is serialized to protobuf via `From<ValidResourceBounds> for protobuf::ResourceBounds`. For `AllResources`, all three fields are emitted, including `l2_gas: Some(zero)` and `l1_data_gas: Some(zero)`. [3](#0-2) 

5. The receiving node deserializes via `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. Because `l1_data_gas.is_zero() && l2_gas.is_zero()` is true, it returns `ValidResourceBounds::L1Gas(l1_gas)` — silently discarding the `AllResources` type. [4](#0-3) 

6. The receiving node calls `calculate_transaction_hash` on the resulting `InvokeTransactionV3`. `get_tip_resource_bounds_hash` now takes the `L1Gas` branch, which does **not** append `L1_DATA_GAS` to the hash chain. The preimage is `Poseidon(tip, L1_GAS, L2_GAS=0)` → hash **H₂ ≠ H₁**. [5](#0-4) 

The two nodes now hold the same transaction under different hashes. The transaction commitment in the block header (which is a Merkle root over transaction hashes) will differ between the proposer and any validator that received the transaction via P2P, causing block-hash disagreement and consensus failure.

---

### Impact Explanation

**High — Transaction conversion or signature/hash logic binds the wrong hash or executable payload.**

Any `AllResources` V3 transaction with zero `l2_gas` and zero `l1_data_gas` that crosses a P2P boundary will be stored under a different hash on the receiving node. Because the transaction commitment in `calculate_block_hash` is computed from transaction hashes, the proposer and validators will compute different block hashes for the same block content, breaking consensus. Additionally, RPC calls that return the transaction hash (e.g., `starknet_getTransactionByHash`) will return different values depending on which node is queried.

---

### Likelihood Explanation

**Medium.** The gateway explicitly accepts `AllResourceBounds` with zero `l2_gas` and zero `l1_data_gas` (the stateless validator test suite includes `valid_l1_gas` with exactly this shape). Any user who submits a V3 transaction specifying only L1 gas bounds (a common pattern for pre-0.13.3-style transactions submitted via the V3 API) will trigger this path. The P2P sync path is exercised on every block.

---

### Recommendation

Replace the value-based heuristic in `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` with an explicit type tag, or always deserialize into `AllResources` when all three fields are present (regardless of whether they are zero). The `TODO(Shahak)` comment already acknowledges this is a temporary workaround for 0.13.2 compatibility; the fix is to assert `l1_data_gas` is not `None` and always produce `AllResources` when it is present, matching the serialization side.

Alternatively, `InvokeTransactionV3Trait::resource_bounds()` for `InvokeTransactionV3` should preserve the original variant rather than re-deriving it from field values.

---

### Proof of Concept

```
1. Submit RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds { l1_gas: {max_amount:1, max_price:1}, l2_gas: {0,0}, l1_data_gas: {0,0} }

2. Originating node computes hash H1:
     tip_resource_bounds_hash = Poseidon(tip, pack(L1_GAS,1,1), pack(L2_GAS,0,0), pack(L1_DATA_GAS,0,0))
     H1 = Poseidon(INVOKE, version, sender, tip_resource_bounds_hash, ...)

3. Transaction serialized to protobuf:
     ResourceBounds { l1_gas: Some({1,1}), l2_gas: Some({0,0}), l1_data_gas: Some({0,0}) }

4. Receiving node deserializes:
     l1_data_gas.is_zero() && l2_gas.is_zero() == true
     → ValidResourceBounds::L1Gas({1,1})

5. Receiving node computes hash H2:
     tip_resource_bounds_hash = Poseidon(tip, pack(L1_GAS,1,1), pack(L2_GAS,0,0))   // L1_DATA_GAS absent
     H2 = Poseidon(INVOKE, version, sender, tip_resource_bounds_hash, ...)

6. H1 ≠ H2 → block commitment mismatch → consensus failure
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L616-628)
```rust
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

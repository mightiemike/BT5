### Title
`ValidResourceBounds` Variant Collapse in Protobuf Deserialization Produces Divergent Transaction Hash Preimage — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` silently collapses an `AllResources` transaction into the `L1Gas` variant when `l2_gas` and `l1_data_gas` are both zero. The transaction hash function `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` element in the Poseidon preimage only for `AllResources`, not for `L1Gas`. A transaction submitted via the gateway (always `AllResources`) and the same transaction deserialized from a p2p block-sync protobuf message (collapsed to `L1Gas`) therefore produce two different transaction hashes from identical field values.

### Finding Description

**Root cause — protobuf deserializer, `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`:**

```
crates/apollo_protobuf/src/converters/transaction.rs  lines 417-436
``` [1](#0-0) 

The logic at line 431 is:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant A
} else {
    ValidResourceBounds::AllResources(...)       // ← variant B
})
```

**Hash function that branches on the variant:**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` (lines 188–211) builds the Poseidon preimage differently for each variant: [2](#0-1) 

- `L1Gas` → preimage = `[tip, L1_GAS_concat, L2_GAS_concat]` (2 resource elements)
- `AllResources` → preimage = `[tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat]` (3 resource elements)

Even when `l1_data_gas = 0`, the `AllResources` branch appends a zero-valued `L1_DATA_GAS_concat` element, producing a strictly different Poseidon output.

**Gateway path always uses `AllResources`:**

`RpcInvokeTransactionV3` stores `resource_bounds: AllResourceBounds` (never `L1Gas`). [3](#0-2) 

The `InternalRpcInvokeTransactionV3` trait implementation hard-wraps it: [4](#0-3) 

The hash is computed at gateway ingestion time via `calculate_transaction_hash` using the `AllResources` variant, so the stored `tx_hash` always includes the L1_DATA_GAS element. [5](#0-4) 

**P2P block-sync path can produce `L1Gas`:**

When a peer sends a block, `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` calls `ValidResourceBounds::try_from(...)`: [6](#0-5) 

If the transaction has `l2_gas = {max_amount:0, max_price_per_unit:0}` and `l1_data_gas = {max_amount:0, max_price_per_unit:0}`, the deserializer produces `L1Gas`. Any subsequent call to `calculate_transaction_hash` on this `InvokeTransactionV3` (e.g., for hash verification, RPC serving, or re-execution) computes a hash that omits the L1_DATA_GAS element — diverging from the hash stored in the block.

The same divergence exists in the RPC-layer conversion `From<ResourceBoundsMapping> for ValidResourceBounds`: [7](#0-6) 

### Impact Explanation

A syncing node that recomputes a transaction hash from a deserialized `InvokeTransactionV3` (e.g., during `validate_transaction_hash`, RPC `starknet_getTransactionByHash`, or fee estimation) will obtain a hash that differs from the canonical hash stored in the block. This matches:

- **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value**: the RPC layer can serve a recomputed hash that does not match the on-chain hash.
- **High — Transaction conversion or signature/hash logic binds the wrong hash or executable payload**: the blockifier/OS receives an `InvokeTransactionV3` whose `resource_bounds` variant is `L1Gas` instead of `AllResources`, causing the hash preimage to omit the L1_DATA_GAS field.

### Likelihood Explanation

The gateway's stateless validator checks `l2_gas.max_price_per_unit >= min_gas_price`. If `min_gas_price > 0` (production default) and `ResourceBounds::is_zero()` tests both `max_amount` and `max_price_per_unit`, then a valid gateway-submitted transaction cannot have `l2_gas.is_zero() = true`, preventing the collapse. However:

1. The `validate_resource_bounds` flag can be set to `false` in config, bypassing the check entirely.
2. If `is_zero()` tests only `max_amount`, a transaction with `l2_gas = {max_amount:0, max_price_per_unit:1}` passes the price check yet still collapses to `L1Gas` on deserialization.
3. The TODO comment at line 427 (`// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2`) shows the `l1_data_gas` field is intentionally optional, widening the collapse window for legacy-format messages.

### Recommendation

1. In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, replace the `L1Gas`/`AllResources` heuristic with an explicit version tag or a dedicated protobuf field that encodes the original variant, so deserialization is lossless.
2. Alternatively, always deserialize into `AllResources` when all three resource fields are present in the protobuf message (even if zero), reserving `L1Gas` only for messages that genuinely omit `l2_gas` and `l1_data_gas`.
3. Add a round-trip test asserting that `hash(gateway_path(tx)) == hash(p2p_sync_path(tx))` for transactions with `l2_gas = 0` and `l1_data_gas = 0`.

### Proof of Concept

```
1. Craft an InvokeV3 transaction with:
     l1_gas  = { max_amount: 1000, max_price_per_unit: 1 }
     l2_gas  = { max_amount: 0,    max_price_per_unit: 0 }
     l1_data_gas = { max_amount: 0, max_price_per_unit: 0 }

2. Submit via gateway:
   - RpcInvokeTransactionV3.resource_bounds = AllResourceBounds { l1_gas, l2_gas=0, l1_data_gas=0 }
   - InternalRpcInvokeTransactionV3::resource_bounds() returns ValidResourceBounds::AllResources(...)
   - get_tip_resource_bounds_hash hashes [tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat(=0)]
   - Stored tx_hash = H_allresources

3. Block is propagated via p2p; peer deserializes InvokeV3 protobuf:
   - l1_data_gas.is_zero() = true, l2_gas.is_zero() = true
   - TryFrom produces ValidResourceBounds::L1Gas(l1_gas)
   - get_tip_resource_bounds_hash hashes [tip, L1_GAS_concat, L2_GAS_concat]  (no L1_DATA_GAS)
   - Recomputed hash = H_l1gas  ≠  H_allresources

4. validate_transaction_hash(tx, block_number, chain_id, H_allresources) on the syncing node
   recomputes H_l1gas and fails to find H_allresources in possible_hashes → hash mismatch.
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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L593-598)
```rust
impl TryFrom<protobuf::InvokeV3> for InvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::InvokeV3) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

**File:** crates/apollo_rpc/src/v0_8/transaction.rs (L188-199)
```rust
impl From<ResourceBoundsMapping> for ValidResourceBounds {
    fn from(value: ResourceBoundsMapping) -> Self {
        if value.l1_data_gas.is_zero() && value.l2_gas.is_zero() {
            Self::L1Gas(value.l1_gas)
        } else {
            Self::AllResources(AllResourceBounds {
                l1_gas: value.l1_gas,
                l1_data_gas: value.l1_data_gas,
                l2_gas: value.l2_gas,
            })
        }
    }
```

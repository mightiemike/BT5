### Title
Protobuf `ValidResourceBounds` Deserialization Silently Collapses `AllResources` to `L1Gas` When L2/Data Gas Are Zero, Producing a Non-Canonical Transaction Hash - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter in `crates/apollo_protobuf/src/converters/transaction.rs` produces `ValidResourceBounds::L1Gas` whenever `l2_gas` and `l1_data_gas` are both zero — even when the original transaction was submitted and hashed as `ValidResourceBounds::AllResources`. Because `get_tip_resource_bounds_hash` hashes a structurally different number of resource felts for `L1Gas` (2 felts) versus `AllResources` (3 felts), the transaction hash computed from the protobuf-deserialized object diverges from the hash computed at the gateway. This is a direct analog to the MarmoStork bug: just as MarmoStork used the unchecked length of a `bytes` argument to generate bytecode, the sequencer uses the `ValidResourceBounds` variant — which is silently rewritten during deserialization — to determine the hash preimage structure.

---

### Finding Description

**Step 1 — Gateway path (correct hash)**

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds` (type-enforced). [1](#0-0) 

When converted to `InternalRpcInvokeTransactionV3`, the `InvokeTransactionV3Trait::resource_bounds()` implementation unconditionally wraps it as `ValidResourceBounds::AllResources`: [2](#0-1) 

`get_tip_resource_bounds_hash` then hashes **three** resource felts (L1_GAS, L2_GAS, L1_DATA_GAS): [3](#0-2) 

The L1_DATA_GAS felt is non-zero even when the bound is zero, because `get_concat_resource` encodes the 7-byte resource name `b"L1_DATA"` into the felt: [4](#0-3) 

**Step 2 — Protobuf deserialization path (wrong hash)**

When the same transaction is serialized to protobuf and deserialized by a P2P sync peer, `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` applies this logic:

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← wrong variant
} else {
    ValidResourceBounds::AllResources(...)
})
``` [5](#0-4) 

For any transaction with `l2_gas = 0` and `l1_data_gas = 0`, the deserialized variant is `L1Gas`, not `AllResources`.

**Step 3 — Hash divergence**

`get_tip_resource_bounds_hash` for `L1Gas` hashes only **two** resource felts (L1_GAS, L2_GAS): [6](#0-5) 

The `AllResources` path adds a third felt for L1_DATA_GAS. Even when `l1_data_gas = 0`, `get_concat_resource(zero, L1_DATA_GAS)` produces a non-zero felt because the 7-byte resource name `b"L1_DATA"` is packed into the upper bits:

```
concat_bytes = [0x00, 'L','1','_','D','A','T','A', 0x00×16 (amount), 0x00×16 (price)]
             = Felt(0x004c315f44415441_0000000000000000_00000000000000000000000000000000)
```

This felt is non-zero, so `poseidon(tip, l1_gas_felt, l2_gas_felt, l1_data_gas_felt)` ≠ `poseidon(tip, l1_gas_felt, l2_gas_felt)`.

**Step 4 — The `InvokeTransactionV3` struct used in block sync carries `ValidResourceBounds`**

The block-sync protobuf path deserializes directly into `InvokeTransactionV3`, whose `resource_bounds` field is `ValidResourceBounds`: [7](#0-6) 

The `TransactionHasher` implementation for `InvokeTransactionV3` calls `get_invoke_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash` with whatever variant was deserialized: [8](#0-7) 

---

### Impact Explanation

A user submits `InvokeV3` with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`. The gateway computes hash **H1** (3-felt preimage). The transaction is included in a block. A syncing peer deserializes the transaction from protobuf, obtains `ValidResourceBounds::L1Gas`, and computes hash **H2** (2-felt preimage). H1 ≠ H2.

- If the peer validates the transaction hash against the block's transaction commitment, it rejects a valid block — **High: valid transactions/blocks rejected before sequencing or during sync**.
- If the peer stores the transaction without revalidating, it stores it under the wrong hash — **High: transaction conversion binds the wrong hash**.

The `ValidResourceBounds::L1Gas` comment explicitly marks this variant as "Pre 0.13.3", yet the converter still silently produces it for any post-0.13.3 transaction with zero L2/data gas bounds. [9](#0-8) 

---

### Likelihood Explanation

The trigger condition — `l2_gas = 0` and `l1_data_gas = 0` — is a valid and common configuration. Any user who submits an `InvokeV3` transaction paying only in L1 gas (e.g., a simple transfer with no L2 gas budget) will produce this condition. The TODO comment in the converter (`Assert data gas is not none once we remove support for 0.13.2`) confirms this code path is intentionally kept alive and has not been hardened. [10](#0-9) 

---

### Recommendation

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter should **always** produce `ValidResourceBounds::AllResources` when all three fields are present in the protobuf message, regardless of whether their values are zero. The `L1Gas` variant should only be produced when the message is structurally absent of `l1_data_gas` (i.e., a genuine pre-0.13.3 message). The current heuristic of checking for zero values conflates "old format" with "new format with zero bounds", breaking hash canonicalization.

Alternatively, `get_tip_resource_bounds_hash` should always include all three resource felts regardless of the `ValidResourceBounds` variant, making the hash independent of the variant.

---

### Proof of Concept

```
1. Submit RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      ResourceBounds { max_amount: 100, max_price_per_unit: 1 },
         l2_gas:      ResourceBounds { max_amount: 0,   max_price_per_unit: 0 },
         l1_data_gas: ResourceBounds { max_amount: 0,   max_price_per_unit: 0 },
     }

2. Gateway computes H1 via InternalRpcInvokeTransactionV3::calculate_transaction_hash:
     get_tip_resource_bounds_hash(AllResources { ... }) →
       resource_felts = [
         concat(L1_GAS, 100, 1),          // non-zero
         concat(L2_GAS, 0, 0),            // encodes "L2_GAS" name → non-zero
         concat(L1_DATA_GAS, 0, 0),       // encodes "L1_DATA" name → non-zero ← KEY FELT
       ]
     H1 = poseidon(tip=0, l1_felt, l2_felt, l1_data_felt)

3. Serialize to protobuf::ResourceBounds:
     { l1_gas: Some(100/1), l2_gas: Some(0/0), l1_data_gas: Some(0/0) }

4. Peer deserializes via TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
     l1_data_gas = Some(0/0).unwrap_or_default() → ResourceBounds::default() (zero)
     l2_gas = ResourceBounds { 0, 0 } → is_zero() = true
     l1_data_gas.is_zero() && l2_gas.is_zero() → TRUE
     → ValidResourceBounds::L1Gas(l1_gas)   ← WRONG VARIANT

5. Peer computes H2 via InvokeTransactionV3::calculate_transaction_hash:
     get_tip_resource_bounds_hash(L1Gas { ... }) →
       resource_felts = [
         concat(L1_GAS, 100, 1),
         concat(L2_GAS, 0, 0),
         // L1_DATA_GAS felt ABSENT
       ]
     H2 = poseidon(tip=0, l1_felt, l2_felt)

6. H1 ≠ H2  (differ by the L1_DATA_GAS name-encoding felt)
   → Block sync hash validation fails, or transaction stored under wrong hash.
```

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L550-556)
```rust
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, SizeOf)]
pub struct RpcInvokeTransactionV3 {
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub resource_bounds: AllResourceBounds,
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

**File:** crates/starknet_api/src/transaction_hash.rs (L216-226)
```rust
fn get_concat_resource(
    resource_bounds: &ResourceBounds,
    resource_name: &ResourceName,
) -> Result<Felt, StarknetApiError> {
    let max_amount = resource_bounds.max_amount.0.to_be_bytes();
    let max_price = resource_bounds.max_price_per_unit.0.to_be_bytes();
    let concat_bytes =
        [[0_u8].as_slice(), resource_name.as_slice(), max_amount.as_slice(), max_price.as_slice()]
            .concat();
    Ok(Felt::from_bytes_be(&concat_bytes.try_into().expect("Expect 32 bytes")))
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

**File:** crates/starknet_api/src/transaction.rs (L664-678)
```rust
#[derive(Debug, Clone, Eq, PartialEq, Hash, Deserialize, Serialize, PartialOrd, Ord)]
pub struct InvokeTransactionV3 {
    pub resource_bounds: ValidResourceBounds,
    pub tip: Tip,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
}
```

**File:** crates/starknet_api/src/transaction.rs (L680-688)
```rust
impl TransactionHasher for InvokeTransactionV3 {
    fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
        transaction_version: &TransactionVersion,
    ) -> Result<TransactionHash, StarknetApiError> {
        get_invoke_transaction_v3_hash(self, chain_id, transaction_version)
    }
}
```

**File:** crates/starknet_api/src/transaction/fields.rs (L363-366)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
```

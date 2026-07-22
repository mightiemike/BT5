### Title
`ValidResourceBounds` Protobuf Round-Trip Silently Mutates Transaction Hash Domain — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary
The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion silently collapses `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` into `L1Gas(l1_gas=X)`. Because `get_tip_resource_bounds_hash` produces structurally different Poseidon preimages for the two variants — `AllResources` appends a third element `concat(L1_DATA_GAS, 0)` while `L1Gas` does not — the transaction hash computed before the protobuf round-trip differs from the hash recomputed after it. Any node that re-derives the hash from the deserialized `InvokeTransactionV3` (e.g., during P2P block sync hash validation) will obtain a divergent value, binding the transaction to the wrong hash.

### Finding Description

**Step 1 — Submission and original hash (AllResources path)**

A user submits `RpcInvokeTransactionV3` with `resource_bounds: AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`. The gateway converts it and calls `calculate_transaction_hash`, which reaches `get_invoke_transaction_v3_hash` → `get_tip_resource_bounds_hash`:

```
ValidResourceBounds::AllResources(all_resources) =>
    vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
``` [1](#0-0) 

So the Poseidon preimage is `[tip, concat(L1_GAS,X), concat(L2_GAS,0), concat(L1_DATA_GAS,0)]` → hash **H1**.

**Step 2 — Storage**

The transaction is stored in the DB as `InvokeTransactionV3` with `resource_bounds: ValidResourceBounds::AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)`. [2](#0-1) 

**Step 3 — Protobuf serialization (AllResources → wire)**

`From<ValidResourceBounds> for protobuf::ResourceBounds` faithfully encodes all three fields:

```rust
ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
    protobuf::ResourceBounds {
        l1_gas: Some(l1_gas.into()),
        l2_gas: Some(l2_gas.into()),
        l1_data_gas: Some(l1_data_gas.into()),   // zero, but present
    }
``` [3](#0-2) 

**Step 4 — Protobuf deserialization (wire → L1Gas)**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` applies the discriminant:

```rust
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
// ...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant changes here
} else {
    ValidResourceBounds::AllResources(...)
})
``` [4](#0-3) 

Because both `l2_gas` and `l1_data_gas` are zero, the deserialized transaction carries `ValidResourceBounds::L1Gas(l1_gas=X)`.

**Step 5 — Hash recomputed from deserialized form (L1Gas path)**

`get_tip_resource_bounds_hash` for `L1Gas`:

```rust
ValidResourceBounds::L1Gas(_) => vec![],   // l1_data_gas NOT appended
``` [5](#0-4) 

Preimage is now `[tip, concat(L1_GAS,X), concat(L2_GAS,0)]` → hash **H2 ≠ H1**.

The `InvokeTransactionV3` protobuf path (used for block sync, distinct from the consensus path which safely uses `AllResourceBounds` throughout) is the affected surface: [6](#0-5) 

### Impact Explanation

Any component that re-derives the transaction hash from the deserialized `InvokeTransactionV3` after a protobuf round-trip will compute **H2** while the canonical stored hash is **H1**. Concretely:

- **P2P block sync hash validation**: a receiving node that validates `tx_hash == recompute(tx)` will reject a legitimately produced block containing such a transaction, causing a sync split.
- **RPC tracing / simulation**: if the RPC layer recomputes the hash from the stored `InvokeTransactionV3` (e.g., for `starknet_getTransactionByHash` or trace endpoints), it returns an authoritative-looking wrong hash to callers.

This matches: *"High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."*

### Likelihood Explanation

Any user can submit an `RpcInvokeTransactionV3` with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`. The gateway's stateless validator only requires at least one non-zero bound; `l1_gas=X` satisfies that. No privilege is required. The transaction will be accepted, included in a block, and stored with the `AllResources` variant. The divergence is triggered automatically on the first protobuf round-trip of that block.

### Recommendation

In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, preserve the `AllResources` variant whenever `l1_data_gas` is explicitly present in the wire message, regardless of its value. The `L1Gas` shortcut should only apply when `l1_data_gas` is absent (`None`) — i.e., the pre-0.13.3 wire format — not when it is present but zero:

```rust
let l1_data_gas_opt = value.l1_data_gas;
// ...
Ok(match l1_data_gas_opt {
    None if l2_gas.is_zero() => ValidResourceBounds::L1Gas(l1_gas),
    _ => ValidResourceBounds::AllResources(AllResourceBounds {
        l1_gas,
        l2_gas,
        l1_data_gas: l1_data_gas_opt.unwrap_or_default().try_into()?,
    }),
})
```

This preserves backward compatibility (absent `l1_data_gas` with zero `l2_gas` → `L1Gas`) while preventing the silent variant mutation for new transactions.

### Proof of Concept

```
1. Submit RpcInvokeTransactionV3:
     resource_bounds = AllResourceBounds { l1_gas: 1000, l2_gas: 0, l1_data_gas: 0 }

2. Gateway computes H1 via AllResources path:
     preimage = [tip, concat(L1_GAS,1000), concat(L2_GAS,0), concat(L1_DATA_GAS,0)]
     H1 = poseidon(preimage)

3. Transaction included in block B; stored with ValidResourceBounds::AllResources(...)

4. Block B propagated via P2P:
     InvokeTransactionV3 → protobuf::InvokeV3 → InvokeTransactionV3
     After deserialization: resource_bounds = ValidResourceBounds::L1Gas(1000)

5. Receiving node recomputes hash via L1Gas path:
     preimage = [tip, concat(L1_GAS,1000), concat(L2_GAS,0)]   ← l1_data_gas absent
     H2 = poseidon(preimage)

6. H1 ≠ H2 → hash validation fails → block B rejected by receiving node
```

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L202-208)
```rust
    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });
```

**File:** crates/apollo_storage/src/serialization/serializers.rs (L1351-1364)
```rust
impl StorageSerde for InvokeTransactionV3 {
    fn serialize_into(&self, res: &mut impl std::io::Write) -> Result<(), StorageSerdeError> {
        let mut to_compress: Vec<u8> = Vec::new();
        self.resource_bounds.serialize_into(&mut to_compress)?;
        self.tip.serialize_into(&mut to_compress)?;
        self.signature.serialize_into(&mut to_compress)?;
        self.nonce.serialize_into(&mut to_compress)?;
        self.sender_address.serialize_into(&mut to_compress)?;
        self.calldata.serialize_into(&mut to_compress)?;
        self.nonce_data_availability_mode.serialize_into(&mut to_compress)?;
        self.fee_data_availability_mode.serialize_into(&mut to_compress)?;
        self.paymaster_data.serialize_into(&mut to_compress)?;
        self.account_deployment_data.serialize_into(&mut to_compress)?;
        self.proof_facts.serialize_into(&mut to_compress)?;
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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L471-490)
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
}
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L593-600)
```rust
impl TryFrom<protobuf::InvokeV3> for InvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::InvokeV3) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;

        let tip = Tip(value.tip);
```

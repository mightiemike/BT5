### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Causing Consensus Nodes to Reject Valid V3 Transactions - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a value-based heuristic (`l1_data_gas.is_zero() && l2_gas.is_zero()`) to decide whether to reconstruct `L1Gas` or `AllResources`. A valid V3 transaction submitted with `AllResourceBounds { l1_gas: non-zero, l2_gas: 0, l1_data_gas: 0 }` is accepted by the gateway, serialized to protobuf as `AllResources`, but deserialized on the receiving consensus node as `L1Gas`. The subsequent `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` conversion then hard-fails because it requires `AllResources`, causing the receiving node to reject the transaction entirely. This creates a split between the proposing node (which accepted the transaction) and all receiving validators (which reject it), breaking consensus.

---

### Finding Description

**Root cause — `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`:** [1](#0-0) 

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← wrong for AllResources with zero bounds
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

The intent of the `TODO` comment is clear: `l1_data_gas = None` in the protobuf wire message means a pre-0.13.3 (`L1Gas`) transaction. But the code calls `unwrap_or_default()` first, collapsing `None` and `Some(zero)` into the same value, then uses `is_zero()` to decide the variant. A 0.13.3+ transaction with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` serializes `l1_data_gas = Some(zero)` on the wire, but after `unwrap_or_default()` it is indistinguishable from a pre-0.13.3 transaction that sent `l1_data_gas = None`.

**Serialization side (proposer) — `From<ValidResourceBounds> for protobuf::ResourceBounds`:** [2](#0-1) 

`AllResources` with zero `l1_data_gas` serializes all three fields as `Some(zero)`. The wire bytes are unambiguous. The bug is entirely on the deserialization side.

**Downstream hard failure — `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3`:** [3](#0-2) 

```rust
resource_bounds: match value.resource_bounds {
    ValidResourceBounds::AllResources(bounds) => bounds,
    _ => {
        return Err(StarknetApiError::OutOfRange { string: "resource_bounds".to_string() });
    }
},
```

After the protobuf round-trip downgrades `AllResources` → `L1Gas`, this arm is hit and the conversion returns an error.

**Consensus P2P path that triggers the failure:** [4](#0-3) 

```rust
impl TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3 {
    fn try_from(mut value: protobuf::InvokeV3WithProof) -> Result<Self, Self::Error> {
        let snapi_invoke: InvokeTransactionV3 = value.invoke...?.try_into()?;
        // This conversion can fail only if the resource_bounds are not AllResources.
        Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
    }
}
```

The comment itself acknowledges the invariant that must hold. The protobuf deserializer breaks it.

**Hash divergence (secondary effect):**

Even if the conversion did not hard-fail, `get_tip_resource_bounds_hash` produces a different hash for `L1Gas` vs `AllResources` with the same numeric values: [5](#0-4) 

`L1Gas` hashes `[tip, l1_gas_felt, l2_gas_felt]` (2 resource felts). `AllResources` hashes `[tip, l1_gas_felt, l2_gas_felt, l1_data_gas_felt]` (3 resource felts). `l1_data_gas_felt` is never zero even when the bounds are zero, because `get_concat_resource` encodes the 7-byte resource name `L1_DATA` into the felt. The proposer computes hash H₁ (`AllResources`); any node that reconstructs the transaction from protobuf would compute H₂ (`L1Gas`), H₁ ≠ H₂.

---

### Impact Explanation

A valid V3 transaction accepted by the gateway and included in a block proposal by the proposing node is rejected by every receiving validator during protobuf deserialization. The proposer and all validators disagree on the transaction set, which can stall or fork consensus. The impact matches: **High — Transaction conversion logic binds the wrong type, causing valid transactions to be rejected before sequencing.**

---

### Likelihood Explanation

The gateway explicitly accepts V3 transactions with only `l1_gas` non-zero: [6](#0-5) 

Any user who submits such a transaction (e.g., a legacy-style V3 transaction that sets only `l1_gas`) triggers the bug. No special privileges are required. The condition `l2_gas = 0 && l1_data_gas = 0` is the common case for transactions that do not use L2 gas or data gas.

---

### Recommendation

Replace the value-based heuristic with a presence-based check. The `None` vs `Some(zero)` distinction on the wire is the correct signal:

```rust
// Before unwrap_or_default(), check presence:
Ok(if value.l1_data_gas.is_none() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    let l1_data_gas = value.l1_data_gas.unwrap_or_default();
    let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

This preserves backward compatibility with pre-0.13.3 peers that omit `l1_data_gas` from the wire, while correctly reconstructing `AllResources` for 0.13.3+ transactions that explicitly set `l1_data_gas = 0`.

---

### Proof of Concept

1. Construct a V3 invoke transaction with `AllResourceBounds { l1_gas: ResourceBounds { max_amount: 1000, max_price_per_unit: 1 }, l2_gas: ResourceBounds::default(), l1_data_gas: ResourceBounds::default() }`.
2. Submit to the gateway — accepted (matches the `valid_l1_gas` test case in the stateless validator).
3. The proposing node converts `InternalRpcTransaction` → `ConsensusTransaction::RpcTransaction(RpcInvokeTransaction::V3(...))` → `protobuf::ConsensusTransaction`. The protobuf wire message contains `resource_bounds = { l1_gas: {1000,1}, l2_gas: {0,0}, l1_data_gas: Some({0,0}) }`.
4. The receiving validator calls `TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction`:
   - `protobuf::InvokeV3` → `InvokeTransactionV3`: `ValidResourceBounds::try_from(...)` sees `l1_data_gas.unwrap_or_default().is_zero() && l2_gas.is_zero()` → returns `ValidResourceBounds::L1Gas(l1_gas)`.
   - `InvokeTransactionV3` → `RpcInvokeTransactionV3`: hits the `_ => return Err(OutOfRange)` arm → returns `DEPRECATED_RESOURCE_BOUNDS_ERROR`.
5. The receiving validator rejects the transaction. The proposer's block proposal is invalid from the validators' perspective. Consensus stalls on any block containing such a transaction.

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L586-598)
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L69-82)
```rust
#[rstest]
#[case::valid_l1_gas(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l1_gas: NON_EMPTY_RESOURCE_BOUNDS,
            ..Default::default()
        },
        ..Default::default()
    }
)]
```

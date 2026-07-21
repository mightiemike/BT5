### Title
Protobuf `ValidResourceBounds` round-trip silently downcasts `AllResources` to `L1Gas`, producing a divergent transaction hash preimage — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` reconstructs `ValidResourceBounds::L1Gas` whenever both `l2_gas` and `l1_data_gas` are zero, regardless of whether the original transaction carried `AllResources`. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` element in the Poseidon chain for `AllResources` but omits it for `L1Gas`, the hash preimage changes after a protobuf round-trip, binding the wrong hash to the transaction.

---

### Finding Description

**Serialization side** (`From<ValidResourceBounds> for protobuf::ResourceBounds`): [1](#0-0) 

For `L1Gas`, `l1_data_gas` is set to `ResourceBounds::default()` (all-zero). For `AllResources` with zero `l2_gas` and zero `l1_data_gas`, the wire bytes are identical to the `L1Gas` case.

**Deserialization side** (`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`): [2](#0-1) 

The reconstruction is purely value-driven: if both `l2_gas` and `l1_data_gas` are zero, the result is `L1Gas`, even when the sender originally signed an `AllResources` transaction. The test suite acknowledges this explicitly: [3](#0-2) 

The comment reads: *"If all the fields of `AllResources` are 0 upon serialization, then the deserialized value will be interpreted as the `L1Gas` variant."* The test works around it by injecting non-zero gas values, but the production code path is unguarded.

**Hash divergence** (`get_tip_resource_bounds_hash`): [4](#0-3) 

- `L1Gas` → hash chain contains **2** resource felts: `[L1_GAS_concat, L2_GAS_concat]`
- `AllResources` → hash chain contains **3** resource felts: `[L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat]`

`get_concat_resource` encodes the 7-byte resource name in the upper bits of the felt, so even when `l1_data_gas` amounts are zero, the felt `L1_DATA_GAS_concat` is non-zero. The two hash chains therefore produce different Poseidon digests.

**Trigger path**: A user submits a V3 invoke/declare/deploy-account transaction with `AllResourceBounds { l1_gas: <nonzero>, l2_gas: zero, l1_data_gas: zero }`. The gateway accepts it (the stateless validator permits zero l2/data-gas bounds): [5](#0-4) 

The gateway computes hash H₁ using the `AllResources` preimage. The transaction is serialized to protobuf for P2P sync. On the receiving node, `TryFrom` reconstructs `L1Gas`, and any subsequent hash computation (e.g., during block re-execution or transaction commitment verification) produces hash H₂ ≠ H₁.

---

### Impact Explanation

The `ValidResourceBounds` variant is part of the signed transaction hash preimage. Silently changing the variant after a protobuf round-trip means:

1. **Wrong transaction hash**: The hash stored or recomputed on a synced node differs from the hash the user signed and the proposer committed. This breaks transaction commitment verification.
2. **Wrong gas computation mode**: `L1Gas` selects `GasVectorComputationMode::NoL2Gas`; `AllResources` selects `All`. Fee-checking and execution use different code paths depending on this mode.

This matches the impact category: *"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."*

---

### Likelihood Explanation

Any V3 transaction with zero `l2_gas` and zero `l1_data_gas` bounds triggers the downcast. The gateway explicitly accepts such transactions. The condition is reachable by any unprivileged user submitting a standard V3 transaction with only L1 gas bounds set.

---

### Recommendation

Preserve the `AllResources` vs `L1Gas` distinction explicitly in the protobuf encoding. Options:

1. **Add a discriminant field** to `protobuf::ResourceBounds` (e.g., a boolean `is_all_resources`) so the variant can be faithfully reconstructed without relying on zero-value heuristics.
2. **Use separate protobuf message types** for `L1Gas` and `AllResources` (a `oneof` in the proto schema).
3. At minimum, change the deserialization logic to reconstruct `AllResources` whenever `l1_data_gas` is present in the wire message (even if zero), reserving `L1Gas` only for the legacy case where `l1_data_gas` is absent (`None`).

---

### Proof of Concept

```
1. Construct a V3 invoke transaction with:
     resource_bounds = AllResourceBounds {
         l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
     }

2. Compute H₁ = get_invoke_transaction_v3_hash(tx, chain_id, version)
   → get_tip_resource_bounds_hash uses AllResources path → 3-element chain
   → H₁ includes L1_DATA_GAS_concat felt

3. Serialize tx.resource_bounds via From<ValidResourceBounds> for protobuf::ResourceBounds
   → l2_gas = zero, l1_data_gas = zero on the wire

4. Deserialize via TryFrom<protobuf::ResourceBounds> for ValidResourceBounds
   → l1_data_gas.is_zero() && l2_gas.is_zero() == true
   → returns ValidResourceBounds::L1Gas(l1_gas)   ← WRONG VARIANT

5. Compute H₂ = get_invoke_transaction_v3_hash(deserialized_tx, chain_id, version)
   → get_tip_resource_bounds_hash uses L1Gas path → 2-element chain
   → H₂ omits L1_DATA_GAS_concat felt

6. Assert H₁ ≠ H₂  ← hash domain mismatch confirmed
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

**File:** crates/apollo_protobuf/src/converters/consensus_test.rs (L26-48)
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

### Title
Protobuf `ValidResourceBounds` deserialization silently downgrades `AllResources` to `L1Gas`, producing a divergent transaction hash - (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` silently converts an `AllResources` variant with zero `l2_gas` and `l1_data_gas` into the `L1Gas` variant. Because `get_tip_resource_bounds_hash` hashes a different number of resource-bound elements for each variant (2 for `L1Gas`, 3 for `AllResources`), the transaction hash computed after a protobuf round-trip differs from the hash computed at the gateway. A valid V3 transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is accepted and hashed as `AllResources` at ingress, but any node that deserializes it from a P2P protobuf message recomputes a different hash, causing hash-validation failure or wrong committed state.

### Finding Description

**Serialization boundary — `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`**

```rust
// crates/apollo_protobuf/src/converters/transaction.rs  lines 417-436
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // silently defaults to zero
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)          // ← variant is CHANGED
        } else {
            ValidResourceBounds::AllResources(...)
        })
    }
}
``` [1](#0-0) 

When a peer sends a transaction whose `l1_data_gas` field is absent (legacy 0.13.2 peer) **or** when the field is present but zero, and `l2_gas` is also zero, the deserializer silently produces `L1Gas` regardless of the original variant.

**Hash domain — `get_tip_resource_bounds_hash`**

```rust
// crates/starknet_api/src/transaction_hash.rs  lines 188-211
pub fn get_tip_resource_bounds_hash(resource_bounds: &ValidResourceBounds, tip: &Tip) -> ... {
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];
    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],                    // 2 elements
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 elements
        }
    });
    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
``` [2](#0-1) 

`L1Gas` hashes `[tip, L1, L2=0]` (2 resource elements); `AllResources` hashes `[tip, L1, L2=0, L1_DATA=0]` (3 resource elements). The Poseidon output is different even when the numeric values of the zero fields are identical.

**Gateway always uses `AllResources` for RPC transactions**

```rust
// crates/starknet_api/src/rpc_transaction.rs  lines 669-676
impl TransactionHasher for InternalRpcInvokeTransactionV3 {
    fn calculate_transaction_hash(...) -> Result<TransactionHash, StarknetApiError> {
        get_invoke_transaction_v3_hash(self, chain_id, transaction_version)
    }
}
```

`InternalRpcInvokeTransactionV3.resource_bounds` is `AllResourceBounds`, and its `InvokeTransactionV3Trait` impl wraps it as `ValidResourceBounds::AllResources(self.resource_bounds)`. The gateway therefore always computes the 3-element hash. [3](#0-2) 

**Serialization round-trip confirms the downgrade**

```rust
// crates/apollo_protobuf/src/converters/transaction.rs  lines 471-489
impl From<ValidResourceBounds> for protobuf::ResourceBounds {
    fn from(value: ValidResourceBounds) -> Self {
        match value {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
                protobuf::ResourceBounds {
                    l1_gas: Some(l1_gas.into()),
                    l2_gas: Some(l2_gas.into()),
                    l1_data_gas: Some(l1_data_gas.into()),   // serialized as Some(zero)
                },
            ...
        }
    }
}
``` [4](#0-3) 

`AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` serializes with `l1_data_gas = Some(zero)`. On deserialization `l1_data_gas.is_zero() && l2_gas.is_zero()` is `true`, so the result is `L1Gas(X)`. The variant is permanently lost.

**Gateway validation permits zero L2/L1_DATA bounds**

The stateless validator accepts `AllResourceBounds` with only `l1_gas` non-zero (test case `valid_l1_gas`), so the triggering transaction is admitted without any special privilege. [5](#0-4) 

### Impact Explanation

Any node that receives a block via P2P state-sync and deserializes a V3 transaction with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` will:

1. Reconstruct the transaction as `L1Gas(X)` (2-element hash preimage).
2. Compute a hash that differs from the one stored in the block header / receipt.
3. Either reject the block (sync stall) or commit the transaction under the wrong hash (wrong state, wrong receipt, wrong event index).

This matches **Critical – Wrong state, receipt, or storage value from accepted input** and **High – RPC/state-sync returns an authoritative-looking wrong value**.

### Likelihood Explanation

- No privilege required: any user can submit a V3 invoke with `l2_gas = 0` and `l1_data_gas = 0`.
- The gateway accepts such transactions (zero bounds are valid).
- The divergence is deterministic and reproducible on every syncing peer.
- The TODO comment (`// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2`) confirms the downgrade logic is intentional for backward compatibility but has not been gated on a version check, leaving it permanently active. [6](#0-5) 

### Recommendation

Replace the value-based variant selection with an explicit version/presence gate:

```rust
// Option A: require l1_data_gas once 0.13.2 support is dropped
let l1_data_gas = value.l1_data_gas
    .ok_or(missing("ResourceBounds::l1_data_gas"))?
    .try_into()?;
Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))

// Option B: preserve the variant by adding an explicit tag field to the protobuf message
// so the deserializer knows whether the sender intended L1Gas or AllResources.
```

The hash function `get_tip_resource_bounds_hash` must be the single source of truth for which variant a transaction carries; the protobuf layer must not silently change that variant based on numeric values.

### Proof of Concept

```
1. Submit RPC invoke V3 with resource_bounds = AllResourceBounds {
       l1_gas: { max_amount: 1000, max_price_per_unit: 1 },
       l2_gas: { max_amount: 0,    max_price_per_unit: 0 },
       l1_data_gas: { max_amount: 0, max_price_per_unit: 0 }
   }

2. Gateway computes hash H_all = poseidon([INVOKE, v3, sender,
       poseidon([tip, concat(L1_GAS,1000,1), concat(L2_GAS,0,0), concat(L1_DATA_GAS,0,0)]),
       ...])

3. Transaction is included in block B. Block header records H_all.

4. Syncing peer receives block B via P2P protobuf.
   Deserializer: l1_data_gas.is_zero() && l2_gas.is_zero() → L1Gas(l1_gas)

5. Peer computes hash H_l1 = poseidon([INVOKE, v3, sender,
       poseidon([tip, concat(L1_GAS,1000,1), concat(L2_GAS,0,0)]),   ← only 2 elements
       ...])

6. H_all ≠ H_l1  →  hash validation fails / wrong hash stored.
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L669-677)
```rust
impl TransactionHasher for InternalRpcInvokeTransactionV3 {
    fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
        transaction_version: &TransactionVersion,
    ) -> Result<TransactionHash, StarknetApiError> {
        get_invoke_transaction_v3_hash(self, chain_id, transaction_version)
    }
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

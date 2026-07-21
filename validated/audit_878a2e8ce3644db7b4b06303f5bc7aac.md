### Title
`ValidResourceBounds` Protobuf Round-Trip Silently Downgrades `AllResources` to `L1Gas` When l2_gas and l1_data_gas Are Zero, Causing Consensus Transaction Deserialization Failure and Wrong Transaction Hash Binding - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a value-based heuristic to reconstruct the variant: if both `l2_gas` and `l1_data_gas` are zero it emits `ValidResourceBounds::L1Gas`, otherwise `ValidResourceBounds::AllResources`. A V3 transaction submitted with `AllResources{l1_gas=X, l2_gas=0, l1_data_gas=0}` is accepted by the gateway and hashed with three resource-bound elements. After the proposer serialises it into a consensus protobuf and a validator deserialises it, the variant is silently changed to `L1Gas`. The downstream `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` conversion then hard-fails because it requires `AllResources`, returning `DEPRECATED_RESOURCE_BOUNDS_ERROR`. The validator cannot reconstruct the transaction and rejects the proposal. The developers acknowledge the issue in a test comment but only work around it in tests.

### Finding Description

**Root cause — `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`:**

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← wrong variant
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

The serialiser (`From<ValidResourceBounds> for protobuf::ResourceBounds`) always emits all three fields, including zero-valued ones: [2](#0-1) 

So the round-trip is **not lossless**: `AllResources{l1_gas=X, l2_gas=0, l1_data_gas=0}` serialises to `{l1_gas=X, l2_gas=0, l1_data_gas=0}` and deserialises back as `L1Gas(X)`.

**Hash divergence.** `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` element only for `AllResources`:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
``` [3](#0-2) 

`AllResources` hashes `[tip, L1_GAS, L2_GAS, L1_DATA_GAS]` (4 elements); `L1Gas` hashes `[tip, L1_GAS, L2_GAS]` (3 elements). The two produce different Poseidon digests, so the `tx_hash` stored in `InternalRpcTransaction` at the gateway diverges from any hash a validator would compute after deserialisation.

**Downstream hard failure.** After the bad deserialisation, `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` rejects the `L1Gas` variant:

```rust
resource_bounds: match value.resource_bounds {
    ValidResourceBounds::AllResources(bounds) => bounds,
    _ => {
        return Err(StarknetApiError::OutOfRange { string: "resource_bounds".to_string() });
    }
},
``` [4](#0-3) 

The consensus converter wraps this as `DEPRECATED_RESOURCE_BOUNDS_ERROR`:

```rust
Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
``` [5](#0-4) 

**The developers acknowledge the issue in a test comment but only patch it in tests:**

```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(transactions: &mut [ConsensusTransaction]) {
    ...
    resource_bounds.l2_gas.max_amount = GasAmount(1);  // workaround
``` [6](#0-5) 

No equivalent guard exists in the production gateway or mempool admission path.

**Gateway accepts the trigger transaction.** The stateless validator accepts `AllResourceBounds` with only `l1_gas` non-zero: [7](#0-6) 

The `InternalRpcInvokeTransactionV3` and `InternalRpcDeclareTransactionV3` store `resource_bounds: AllResourceBounds` (not `ValidResourceBounds`), so the gateway-side hash is always computed as `AllResources`: [8](#0-7) 

### Impact Explanation

A user submits a V3 invoke transaction with `AllResourceBounds{l1_gas=X, l2_gas=0, l1_data_gas=0}`. The gateway accepts it and stores it with hash H (AllResources, 4-element Poseidon). The proposer converts it to `ConsensusTransaction`, serialises to protobuf (succeeds), and broadcasts the proposal. Every validator that deserialises the proposal hits `DEPRECATED_RESOURCE_BOUNDS_ERROR` and cannot reconstruct the transaction. The proposal is rejected. If the attacker continuously injects such transactions, the proposer keeps including them and validators keep rejecting proposals, stalling consensus. This matches the allowed impact: **"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload"** and **"Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."**

### Likelihood Explanation

The trigger is a single valid RPC call with a standard V3 transaction format where `l2_gas` and `l1_data_gas` amounts and prices are all zero. No special privilege, no malformed bytes, no peer-only path. Any unprivileged user can submit it. The gateway's stateless validator explicitly allows it. The only mitigation is the test-only workaround that is not enforced in production.

### Recommendation

Replace the value-based heuristic with an explicit wire-level discriminator. Options:

1. **Add a boolean tag to the protobuf `ResourceBounds` message** (e.g., `bool is_all_resources`) so the variant is preserved unambiguously across the wire.
2. **Enforce the test workaround in production**: reject at the gateway any V3 transaction where both `l2_gas` and `l1_data_gas` are entirely zero (amount and price), since such a transaction is semantically equivalent to a pre-0.13.3 `L1Gas` transaction and should use that variant.
3. **Extend the stateless validator** to require at least one of `l2_gas.max_amount > 0` or `l1_data_gas.max_amount > 0` for V3 `AllResources` transactions, matching the invariant the protobuf deserialiser assumes.

### Proof of Concept

```
1. Craft RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
     }
   All other fields valid (correct nonce, signature over the AllResources hash).

2. Submit via starknet_addInvokeTransaction.
   Gateway accepts; tx_hash H = Poseidon(INVOKE, v3, sender, tip||L1_GAS||L2_GAS||L1_DATA_GAS, ...).

3. Transaction enters mempool with hash H.

4. Proposer pulls tx, calls convert_internal_consensus_tx_to_consensus_tx → succeeds.
   Serialises to protobuf::ConsensusTransaction with ResourceBounds{l1_gas=1000/1, l2_gas=0/0, l1_data_gas=0/0}.

5. Validator receives protobuf, calls TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
     l1_data_gas.is_zero() && l2_gas.is_zero() == true
     → ValidResourceBounds::L1Gas(ResourceBounds{1000,1})   ← wrong variant

6. TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3 hits the L1Gas arm:
     return Err(StarknetApiError::OutOfRange { string: "resource_bounds" })
   Mapped to DEPRECATED_RESOURCE_BOUNDS_ERROR.

7. TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction returns Err.
   Validator cannot process the proposal → proposal rejected.

8. Repeat from step 3 to stall consensus indefinitely.
```

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L431-435)
```rust
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
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

**File:** crates/starknet_api/src/transaction_hash.rs (L203-208)
```rust
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L591-598)
```rust
            resource_bounds: match value.resource_bounds {
                ValidResourceBounds::AllResources(bounds) => bounds,
                _ => {
                    return Err(StarknetApiError::OutOfRange {
                        string: "resource_bounds".to_string(),
                    });
                }
            },
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L130-130)
```rust
        Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
```

**File:** crates/apollo_protobuf/src/converters/consensus_test.rs (L26-44)
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
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L70-82)
```rust
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

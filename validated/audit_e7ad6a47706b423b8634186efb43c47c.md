### Title
`ValidResourceBounds` Variant Reconstructed via Value-Based Heuristic Across Version Boundary Produces Wrong Transaction Hash — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf-to-`ValidResourceBounds` conversion uses a zero-value heuristic to decide between the `L1Gas` (pre-0.13.3) and `AllResources` (post-0.13.3) variants. For a post-0.13.3 transaction whose `l2_gas` and `l1_data_gas` are legitimately zero, the heuristic silently downgrades the variant from `AllResources` to `L1Gas`. Because `get_tip_resource_bounds_hash` produces a **different hash preimage** for each variant (2 vs 3 elements), any node that recomputes the transaction hash from the deserialized object will obtain a value that diverges from the hash the submitter signed and the proposer committed.

### Finding Description

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` at lines 431–435 reads:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

The intent is backward compatibility with Starknet 0.13.2 transactions, which never carried `l1_data_gas`. The TODO comment on line 426 acknowledges this is temporary:

```
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
``` [2](#0-1) 

However, the heuristic is purely value-based, not version-gated. A post-0.13.3 `InvokeTransactionV3` with `ValidResourceBounds::AllResources(AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 })` is a valid transaction accepted by the gateway (the gateway's `RpcInvokeTransactionV3` uses `AllResourceBounds` and all fields may be zero). When serialized to protobuf via `From<ValidResourceBounds> for protobuf::ResourceBounds`, `l1_data_gas` is emitted as `Some(zero)`:

```rust
ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
    protobuf::ResourceBounds {
        l1_gas: Some(l1_gas.into()),
        l2_gas: Some(l2_gas.into()),
        l1_data_gas: Some(l1_data_gas.into()),  // zero, but present
    },
``` [3](#0-2) 

On deserialization the zero-check fires and the variant is downgraded to `L1Gas`. The hash function `get_tip_resource_bounds_hash` then produces a **2-element** Poseidon preimage (L1\_GAS + L2\_GAS) instead of the original **3-element** preimage (L1\_GAS + L2\_GAS + L1\_DATA\_GAS):

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // ← 2 elements
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // ← 3 elements
    }
});
``` [4](#0-3) 

The resulting hash diverges from the hash the user signed and the proposer stored.

The consensus path is unaffected because `ConsensusTransaction` uses `RpcTransaction → AllResourceBounds` and the deserialization goes through `TryFrom<protobuf::ResourceBounds> for AllResourceBounds` (which never applies the zero-check heuristic):

```rust
impl TryFrom<protobuf::ResourceBounds> for AllResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        Ok(Self {
            l1_gas: value.l1_gas.ok_or(...)?.try_into()?,
            l2_gas: value.l2_gas.ok_or(...)?.try_into()?,
            l1_data_gas: value.l1_data_gas.ok_or(...)?.try_into()?,
        })
    }
}
``` [5](#0-4) 

The affected path is the **P2P state-sync path**, which uses `FullTransaction → Transaction::Invoke(InvokeTransactionV3)` and therefore goes through `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`.

### Impact Explanation

Any node that receives such a transaction via P2P state sync and calls `validate_transaction_hash` (which calls `get_transaction_hash → get_invoke_transaction_v3_hash → get_tip_resource_bounds_hash`) will compute a hash that does not match the committed hash. This causes:

1. **Wrong hash stored or returned**: the syncing node's view of the transaction hash diverges from the canonical hash, causing `starknet_getTransactionByHash` or receipt lookups to return an authoritative-looking wrong value.
2. **State-sync rejection**: if hash validation is enforced during sync, the block containing such a transaction is rejected, stalling the syncing node.

This maps to: *High — Transaction conversion or signature/hash logic binds the wrong hash* and *High — RPC execution returns an authoritative-looking wrong value*.

### Likelihood Explanation

The trigger is a valid, gateway-accepted transaction with `AllResourceBounds { l1_gas: nonzero, l2_gas: 0, l1_data_gas: 0 }`. The gateway's stateless validator explicitly allows transactions with only `l1_gas` set: [6](#0-5) 

Any user who submits such a transaction (e.g., a simple ETH-fee-only invoke) triggers the divergence on every syncing peer. No privileged access is required.

### Recommendation

Replace the value-based heuristic with a version-aware gate. The protobuf `ResourceBounds` message (or the enclosing transaction message) should carry an explicit flag or the Starknet version should be threaded into the conversion so that post-0.13.3 transactions always deserialize as `AllResources`, regardless of whether `l2_gas` and `l1_data_gas` are zero. Alternatively, remove the `L1Gas` variant entirely from the deserialization path once 0.13.2 support is dropped (as the TODO already anticipates).

### Proof of Concept

1. Submit via gateway: `RpcInvokeTransactionV3 { resource_bounds: AllResourceBounds { l1_gas: 1000, l2_gas: 0, l1_data_gas: 0 }, ... }`.
2. Gateway computes hash H₁ using `AllResources` (3-element Poseidon: tip + L1\_GAS + L2\_GAS + L1\_DATA\_GAS).
3. Transaction is included in block B and committed with hash H₁.
4. A syncing peer receives block B via P2P state sync; the `InvokeTransactionV3` is deserialized from protobuf.
5. `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `L1Gas(1000)`.
6. Peer recomputes hash H₂ using `L1Gas` (2-element Poseidon: tip + L1\_GAS + L2\_GAS). H₂ ≠ H₁.
7. `validate_transaction_hash(tx, block_number, chain_id, H₁, options)` returns `false`; the peer rejects or misindexes the transaction.

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

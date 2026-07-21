### Title
`AllResources` with zero L2/L1DataGas bounds collapses to `L1Gas` in protobuf round-trip, causing consensus block rejection — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf serializer for `ValidResourceBounds::AllResources` with zero `l2_gas` and `l1_data_gas` produces bytes identical to `ValidResourceBounds::L1Gas`. The deserializer uses a zero-value heuristic to decide which variant to reconstruct, so `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` always deserializes as `L1Gas(X)`. The gateway accepts such transactions (treating them as `AllResources`), but when the proposer broadcasts them to validators via P2P protobuf, the validator's `RpcInvokeTransactionV3` conversion fails because it requires `AllResources`. The validator marks the proposal as `Failed`, causing a consensus liveness failure.

---

### Finding Description

**Serialization path** (`From<ValidResourceBounds> for protobuf::ResourceBounds`, lines 471–490):

```rust
ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
    l1_gas: Some(l1_gas.into()),
    l2_gas: Some(value.get_l2_bounds().into()),          // zeros
    l1_data_gas: Some(ResourceBounds::default().into()), // zeros
},
ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
    protobuf::ResourceBounds {
        l1_gas: Some(l1_gas.into()),
        l2_gas: Some(l2_gas.into()),
        l1_data_gas: Some(l1_data_gas.into()),
    },
```

When `AllResources` has `l2_gas = 0` and `l1_data_gas = 0`, the serialized bytes are **identical** to `L1Gas`.

**Deserialization path** (`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, lines 417–437):

```rust
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← wrong variant for AllResources input
} else {
    ValidResourceBounds::AllResources(...)
})
```

**Gateway acceptance**: The stateless validator accepts `AllResourceBounds { l1_gas: NON_EMPTY, l2_gas: 0, l1_data_gas: 0 }` (test case `valid_l1_gas`). The gateway computes the transaction hash using `AllResources` semantics via `get_tip_resource_bounds_hash`, which includes the `L1_DATA_GAS` field in the Poseidon hash chain.

**Consensus P2P failure**: When the proposer broadcasts the block, the transaction is serialized to `protobuf::InvokeV3WithProof`. On the validator side, `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` (lines 115–132 of `rpc_transaction.rs`) calls `snapi_invoke.try_into()` to extract `AllResourceBounds` from the deserialized `InvokeTransactionV3`. Since the deserialized `resource_bounds` is now `L1Gas`, this conversion fails with `DEPRECATED_RESOURCE_BOUNDS_ERROR`:

```rust
// This conversion can fail only if the resource_bounds are not AllResources.
Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
```

The error propagates to `validate_proposal.rs` (lines 602–616):

```rust
let conversion_results = match conversion_results {
    Ok(results) => results,
    Err(e) => {
        return HandledProposalPart::Failed(format!(
            "Failed to convert transactions. Stopping the build of the current proposal. {e:?}"
        ));
    }
};
```

The validator rejects the entire block proposal.

**Hash domain divergence**: Even if the conversion did not fail, the hash computed by the gateway (`AllResources`, includes `L1_DATA_GAS` in Poseidon chain) differs from the hash the validator would compute (`L1Gas`, omits `L1_DATA_GAS`). The `get_tip_resource_bounds_hash` function in `transaction_hash.rs` (lines 188–211) conditionally appends the data-gas field only for `AllResources`:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
```

---

### Impact Explanation

**High. Transaction conversion logic binds the wrong resource bounds type (`L1Gas` instead of `AllResources`), causing validators to reject block proposals.**

A single attacker-controlled transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is sufficient to make every block proposal that includes it fail validation. If the transaction remains in the mempool (the proposer has no way to detect the issue before broadcasting), the proposer will repeatedly include it and repeatedly have proposals rejected, stalling consensus.

---

### Likelihood Explanation

**Medium.** The gateway explicitly accepts `AllResourceBounds` with zero `l2_gas` and `l1_data_gas` (confirmed by the `valid_l1_gas` test case in `stateless_transaction_validator_test.rs`). No additional privilege is required. Any unprivileged user can submit such a transaction via the public RPC endpoint.

---

### Recommendation

1. **Reject at the gateway**: In the stateless validator, reject `AllResourceBounds` where both `l2_gas` and `l1_data_gas` are zero, or canonicalize them to `L1Gas` before hash computation.
2. **Fix the protobuf deserializer**: Add a discriminator field (e.g., a boolean `is_all_resources`) to `protobuf::ResourceBounds` so the variant can be reconstructed unambiguously, independent of zero-value fields.
3. **Alternatively**: In the deserializer, always produce `AllResources` when all three fields are present in the protobuf message (even if zero), and only produce `L1Gas` when `l1_data_gas` is absent (`None`), which is the original 0.13.2 compatibility case indicated by the existing TODO comment.

---

### Proof of Concept

1. Submit to the gateway:
   ```json
   {
     "type": "INVOKE",
     "version": "0x3",
     "resource_bounds": {
       "l1_gas":      { "max_amount": "0x3e8", "max_price_per_unit": "0x1" },
       "l2_gas":      { "max_amount": "0x0",   "max_price_per_unit": "0x0" },
       "l1_data_gas": { "max_amount": "0x0",   "max_price_per_unit": "0x0" }
     },
     ...
   }
   ```
   Gateway accepts; hash H₁ computed with `AllResources` (includes `L1_DATA_GAS=0` in Poseidon chain).

2. Transaction enters mempool. Proposer picks it up and includes it in a block proposal.

3. Proposer serializes via `From<ConsensusTransaction> for protobuf::ConsensusTransaction` → `From<ValidResourceBounds> for protobuf::ResourceBounds`. Wire bytes: `{ l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`.

4. Validator deserializes: `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` → `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas(X)`.

5. `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3`: `snapi_invoke.try_into()` fails → `DEPRECATED_RESOURCE_BOUNDS_ERROR`.

6. `validate_proposal.rs` returns `HandledProposalPart::Failed(...)`. Block rejected. Consensus stalls. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L115-132)
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
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L598-616)
```rust
        Some(ProposalPart::Transactions(TransactionBatch { transactions: txs })) => {
            // TODO(guyn): check that the length of txs and the number of batches we receive is not
            // so big it would fill up the memory (in case of a malicious proposal)
            debug!("Received transaction batch with {} txs", txs.len());
            let conversion_results =
                futures::future::join_all(txs.into_iter().map(|tx| {
                    transaction_converter.convert_consensus_tx_to_internal_consensus_tx(tx)
                }))
                .await
                .into_iter()
                .collect::<Result<Vec<_>, _>>();
            let conversion_results = match conversion_results {
                Ok(results) => results,
                Err(e) => {
                    return HandledProposalPart::Failed(format!(
                        "Failed to convert transactions. Stopping the build of the current \
                         proposal. {e:?}"
                    ));
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

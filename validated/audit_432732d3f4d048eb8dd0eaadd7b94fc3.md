### Title
P2P Protobuf Round-Trip Collapses `AllResources` with Zero L2/Data-Gas into `L1Gas`, Causing Proposal Rejection - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

A V3 invoke transaction with `AllResourceBounds { l1_gas: X, l2_gas: ZERO, l1_data_gas: ZERO }` is accepted by the gateway (hash computed under the `AllResources` variant), but when the proposer broadcasts the block proposal over P2P, the protobuf deserialization on the validator side silently collapses the variant to `ValidResourceBounds::L1Gas`. The subsequent conversion back to `RpcInvokeTransactionV3` then hard-fails because it only accepts `AllResources`, causing every validator to mark the proposal as `Failed`.

---

### Finding Description

**Step 1 – Gateway accepts the transaction.**

`RpcInvokeTransactionV3` always carries `resource_bounds: AllResourceBounds`. The gateway converts it to `InternalRpcInvokeTransactionV3` (same `AllResourceBounds` field) and computes the hash via `get_invoke_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash`. For `ValidResourceBounds::AllResources`, that function hashes **three** resource felts (L1 gas, L2 gas, L1 data gas), even when the latter two are zero. [1](#0-0) 

**Step 2 – Proposer serializes the transaction to protobuf.**

`From<RpcInvokeTransactionV3> for protobuf::InvokeV3WithProof` first converts to `InvokeTransactionV3` (wrapping the bounds as `ValidResourceBounds::AllResources`), then to `protobuf::InvokeV3`. The `From<ValidResourceBounds> for protobuf::ResourceBounds` impl faithfully emits all three fields, including the two zero ones. [2](#0-1) 

**Step 3 – Validator deserializes: variant collapses.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` checks:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← wrong variant
} else {
    ValidResourceBounds::AllResources(...)
})
```

Because both fields are zero, the deserialized value is `ValidResourceBounds::L1Gas(X)` — a different variant than what was originally signed and hashed. [3](#0-2) 

**Step 4 – Conversion to `RpcInvokeTransactionV3` hard-fails.**

`TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` calls `snapi_invoke.try_into()`, which is `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3`. That conversion rejects any non-`AllResources` variant:

```rust
resource_bounds: match value.resource_bounds {
    ValidResourceBounds::AllResources(bounds) => bounds,
    _ => return Err(StarknetApiError::OutOfRange { ... }),
},
``` [4](#0-3) 

The error propagates as `DEPRECATED_RESOURCE_BOUNDS_ERROR` from `TryFrom<protobuf::InvokeV3WithProof>`. [5](#0-4) 

**Step 5 – Proposal is rejected.**

`validate_proposal.rs` collects all conversion results; a single error causes the entire proposal to be marked `Failed`:

```rust
Err(e) => {
    return HandledProposalPart::Failed(format!(
        "Failed to convert transactions. Stopping the build of the current proposal. {e:?}"
    ));
}
``` [6](#0-5) 

---

### Impact Explanation

Any unprivileged user can submit a syntactically valid V3 invoke transaction with `l2_gas = 0` and `l1_data_gas = 0`. The gateway accepts it (the stateless validator explicitly allows `AllResourceBounds` with only `l1_gas` non-zero, as shown by the `valid_l1_gas` test case). Once the transaction reaches the proposer's mempool and is included in a proposal, every validator rejects the proposal. This is a targeted denial-of-service against the proposer: the proposer's round is wasted and consensus must retry.

This matches: **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

---

### Likelihood Explanation

The trigger requires only a single valid-looking RPC call. No privileged access, no special key, no malformed bytes — the transaction passes all gateway checks. The condition (`l2_gas = 0 && l1_data_gas = 0`) is the default value of `AllResourceBounds` and is explicitly tested as valid in the gateway test suite. [7](#0-6) 

---

### Recommendation

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion must not use the zero-value heuristic to decide the variant. The protobuf message should carry an explicit discriminant (e.g., a boolean `is_all_resources` field, or a oneof), or the conversion should always produce `AllResources` when all three fields are present in the message (even if zero), reserving `L1Gas` only for messages that genuinely omit `l1_data_gas`:

```rust
// Current (broken):
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(...)
})

// Fixed: only collapse to L1Gas when l1_data_gas was absent (None), not when it is zero
let l1_data_gas_opt = value.l1_data_gas;
Ok(match l1_data_gas_opt {
    None => ValidResourceBounds::L1Gas(l1_gas),
    Some(dg) => ValidResourceBounds::AllResources(AllResourceBounds {
        l1_gas,
        l2_gas,
        l1_data_gas: dg.try_into()?,
    }),
})
``` [3](#0-2) 

---

### Proof of Concept

1. Submit via RPC:
   ```json
   {
     "type": "INVOKE",
     "version": "0x3",
     "resource_bounds": {
       "l1_gas": { "max_amount": "0x100", "max_price_per_unit": "0x1" },
       "l2_gas": { "max_amount": "0x0",   "max_price_per_unit": "0x0" },
       "l1_data_gas": { "max_amount": "0x0", "max_price_per_unit": "0x0" }
     },
     ...
   }
   ```
2. Gateway accepts: `AllResourceBounds { l1_gas: 0x100@0x1, l2_gas: ZERO, l1_data_gas: ZERO }` passes stateless validation.
3. Transaction enters the proposer's mempool.
4. Proposer includes it in a proposal and broadcasts via P2P.
5. Each validator calls `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `L1Gas`.
6. `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` returns `Err(DEPRECATED_RESOURCE_BOUNDS_ERROR)`.
7. `convert_consensus_tx_to_internal_consensus_tx` propagates the error.
8. `validate_proposal` returns `HandledProposalPart::Failed(...)`.
9. The proposer's block is rejected; consensus round is wasted. [5](#0-4) [4](#0-3)

### Citations

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L586-611)
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
            signature: value.signature,
            nonce: value.nonce,
            tip: value.tip,
            paymaster_data: value.paymaster_data,
            nonce_data_availability_mode: value.nonce_data_availability_mode,
            fee_data_availability_mode: value.fee_data_availability_mode,
            sender_address: value.sender_address,
            calldata: value.calldata,
            account_deployment_data: value.account_deployment_data,
            proof_facts: value.proof_facts,
            proof: Proof::default(),
        })
    }
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L609-616)
```rust
            let conversion_results = match conversion_results {
                Ok(results) => results,
                Err(e) => {
                    return HandledProposalPart::Failed(format!(
                        "Failed to convert transactions. Stopping the build of the current \
                         proposal. {e:?}"
                    ));
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

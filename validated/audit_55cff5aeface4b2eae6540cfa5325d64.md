### Title
`ValidResourceBounds` Protobuf Deserialization Heuristic Silently Downgrades `AllResources` to `L1Gas`, Breaking Consensus Transaction Conversion for Valid V3 Transactions - (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion uses a value-based heuristic to distinguish between the `L1Gas` and `AllResources` enum variants: if both `l2_gas` and `l1_data_gas` are zero, it silently produces `ValidResourceBounds::L1Gas`. A valid V3 invoke transaction submitted with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is accepted by the Gateway and assigned a hash computed under the `AllResources` domain (3 resource felts), but when the block proposal is propagated over consensus P2P, the receiving validator's deserialization path converts the resource bounds to `L1Gas`, causing `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` to return `DEPRECATED_RESOURCE_BOUNDS_ERROR` and reject the entire consensus message.

---

### Finding Description

**Step 1 – Serialization preserves `AllResources` correctly.**

When a `ConsensusTransaction` containing an `RpcInvokeTransactionV3` with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is serialized to protobuf, the path is:

`RpcInvokeTransactionV3` → `InvokeTransactionV3` (wraps as `ValidResourceBounds::AllResources`) → `protobuf::InvokeV3` via `From<ValidResourceBounds> for protobuf::ResourceBounds`:

```rust
// crates/apollo_protobuf/src/converters/transaction.rs:479-488
ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
    protobuf::ResourceBounds {
        l1_gas: Some(l1_gas.into()),
        l2_gas: Some(l2_gas.into()),       // Some(zero)
        l1_data_gas: Some(l1_data_gas.into()), // Some(zero)
    }
```

Both zero-valued fields are serialized as `Some(zero)`, not `None`.

**Step 2 – Deserialization applies a broken heuristic.**

On the receiving validator, `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` is called:

```rust
// crates/apollo_protobuf/src/converters/transaction.rs:417-437
let l1_data_gas = value.l1_data_gas.unwrap_or_default(); // Some(zero) → zero
let l1_gas: ResourceBounds = l1_gas.try_into()?;
let l2_gas: ResourceBounds = l2_gas.try_into()?;         // zero
let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?; // zero
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← wrong variant produced
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

Because both `l2_gas` and `l1_data_gas` are zero, the heuristic fires and produces `ValidResourceBounds::L1Gas` even though the original transaction was `AllResources`.

**Step 3 – Downstream conversion fails hard.**

The deserialized `InvokeTransactionV3` (now carrying `L1Gas`) is then converted to `RpcInvokeTransactionV3` via:

```rust
// crates/starknet_api/src/rpc_transaction.rs:586-611
impl TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3 {
    fn try_from(value: InvokeTransactionV3) -> Result<Self, Self::Error> {
        Ok(Self {
            resource_bounds: match value.resource_bounds {
                ValidResourceBounds::AllResources(bounds) => bounds,
                _ => return Err(StarknetApiError::OutOfRange {
                    string: "resource_bounds".to_string(),
                }),  // ← error returned here
            },
            ...
        })
    }
}
```

This returns `DEPRECATED_RESOURCE_BOUNDS_ERROR`, propagating up through `TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction` and causing the entire consensus message to be rejected.

**Step 4 – The codebase acknowledges the ambiguity but does not fix it.**

The test file `crates/apollo_protobuf/src/converters/consensus_test.rs` contains an explicit comment and workaround:

```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(transactions: &mut [ConsensusTransaction]) {
    ...
    resource_bounds.l2_gas.max_amount = GasAmount(1); // forced non-zero to avoid the bug
}
```

This confirms the developers are aware of the ambiguity but have not fixed the production deserialization path.

---

### Impact Explanation

**Impact: High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

A valid V3 invoke transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is:

1. **Accepted** by the Gateway (the Gateway uses `AllResourceBounds` directly, never goes through the broken heuristic).
2. **Assigned a hash** computed under the `AllResources` domain (3 resource felts in `get_tip_resource_bounds_hash`).
3. **Included** in a block proposal by the proposer node.
4. **Rejected** by all receiving validators when they attempt to deserialize the consensus P2P message, because the `L1Gas` variant is produced and `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` returns an error.

The block proposal is dropped by validators, preventing finalization. An unprivileged attacker who can submit a transaction to the Gateway can trigger this by setting `l2_gas = { max_amount: 0, max_price_per_unit: 0 }` and `l1_data_gas = { max_amount: 0, max_price_per_unit: 0 }`.

Additionally, the hash domain divergence means the proposer computes the transaction hash using `AllResources` (3 felts), while any node that reconstructs the hash after deserialization would use `L1Gas` (2 felts), producing a different `tx_hash` — binding the wrong executable payload to the wrong hash.

---

### Likelihood Explanation

**Likelihood: High.**

- The trigger condition (`l2_gas = 0` and `l1_data_gas = 0`, both fields fully zero) is reachable by any user submitting a V3 transaction. Zero gas bounds are a natural choice for transactions that do not intend to pay fees (e.g., bootstrap transactions, test transactions, or transactions relying on fee-free execution).
- The Gateway performs no validation that prevents zero `l2_gas`/`l1_data_gas` bounds on V3 transactions.
- The `AllResourceBounds::create_for_testing()` helper itself uses `max_price_per_unit: GasPrice(1)` (non-zero price), which avoids the bug — but a user submitting `max_price_per_unit: GasPrice(0)` for both fields triggers it.
- No privilege is required; any external user can submit such a transaction.

---

### Recommendation

Replace the value-based heuristic in `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` with an explicit discriminator. The protobuf `ResourceBounds` message should carry a boolean or enum field indicating whether the transaction is `L1Gas`-only or `AllResources`. Until the protobuf schema is updated, the deserialization should default to `AllResources` whenever `l1_data_gas` is present as `Some(...)` in the wire message, regardless of its numeric value:

```rust
// Proposed fix in crates/apollo_protobuf/src/converters/transaction.rs
Ok(if value.l1_data_gas.is_none() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

This preserves backward compatibility with pre-0.13.3 transactions (which genuinely omit `l1_data_gas`) while correctly handling modern `AllResources` transactions that happen to have zero values.

---

### Proof of Concept

**Trigger path:**

1. Submit a V3 invoke transaction to the Gateway with:
   ```json
   {
     "type": "INVOKE",
     "version": "0x3",
     "resource_bounds": {
       "l1_gas":      { "max_amount": "0x1", "max_price_per_unit": "0x1" },
       "l2_gas":      { "max_amount": "0x0", "max_price_per_unit": "0x0" },
       "l1_data_gas": { "max_amount": "0x0", "max_price_per_unit": "0x0" }
     },
     ...
   }
   ```

2. The Gateway accepts the transaction. `convert_rpc_tx_to_internal` computes the hash using `ValidResourceBounds::AllResources` (3 resource felts in `get_tip_resource_bounds_hash`).

3. The proposer includes the transaction in a block proposal and serializes it to `protobuf::ConsensusTransaction::InvokeV3(InvokeV3WithProof { invoke: InvokeV3 { resource_bounds: { l1_gas: Some(1,1), l2_gas: Some(0,0), l1_data_gas: Some(0,0) }, ... } })`.

4. The receiving validator calls `TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction`:
   - `ValidResourceBounds::try_from(resource_bounds)` → `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas(l1_gas)`.
   - `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` → `L1Gas` arm → `Err(DEPRECATED_RESOURCE_BOUNDS_ERROR)`.
   - Consensus message rejected.

**Divergent values:**

| Location | `resource_bounds` variant | `get_tip_resource_bounds_hash` input felts |
|---|---|---|
| Gateway (sender) | `AllResources { l1_gas, l2_gas=0, l1_data_gas=0 }` | 3 felts: L1_GAS, L2_GAS, L1_DATA_GAS |
| Validator (receiver) | `L1Gas(l1_gas)` | conversion fails before hash | [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L1027-1053)
```rust
impl TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ConsensusTransaction) -> Result<Self, Self::Error> {
        let txn = value.txn.ok_or(missing("ConsensusTransaction::txn"))?;
        let txn = match txn {
            protobuf::consensus_transaction::Txn::DeclareV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::Declare(
                    RpcDeclareTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::DeployAccountV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::DeployAccount(
                    RpcDeployAccountTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::InvokeV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::Invoke(
                    RpcInvokeTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::L1Handler(txn) => {
                ConsensusTransaction::L1Handler(txn.try_into()?)
            }
        };
        Ok(txn)
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

**File:** crates/starknet_api/src/transaction_hash.rs (L187-211)
```rust
// An implementation of the SNIP: https://github.com/EvyatarO/SNIPs/blob/snip-8/SNIPS/snip-8.md
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

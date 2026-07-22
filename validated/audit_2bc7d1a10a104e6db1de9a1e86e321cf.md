### Title
P2P Protobuf `ValidResourceBounds` Zero-Value Heuristic Silently Downgrades `AllResources` V3 Invoke Transactions to `L1Gas`, Causing Consensus-Path Rejection of Valid Transactions — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter uses a zero-value heuristic (`l1_data_gas.is_zero() && l2_gas.is_zero()`) to distinguish pre-0.13.3 `L1Gas` transactions from post-0.13.3 `AllResources` transactions. This heuristic is ambiguous: a valid V3 invoke transaction submitted via RPC with `AllResources` bounds where both `l2_gas = 0` and `l1_data_gas = 0` is accepted by the gateway but, after a protobuf round-trip in the consensus P2P path, is silently downgraded to `ValidResourceBounds::L1Gas`. The subsequent conversion from `InvokeTransactionV3` to `RpcInvokeTransactionV3` then unconditionally rejects non-`AllResources` bounds, causing the transaction to be dropped during consensus propagation.

---

### Finding Description

**Step 1 — Serialization is lossless but deserialization is ambiguous.**

`From<ValidResourceBounds> for protobuf::ResourceBounds` serializes `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` by emitting all three fields with their zero values: [1](#0-0) 

The resulting wire bytes are identical to those produced by `L1Gas(l1_gas=X)` (which also emits `l2_gas=0` and `l1_data_gas=default()`).

**Step 2 — Deserialization applies the zero-value heuristic.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converts the received bytes back to `L1Gas` whenever both `l2_gas` and `l1_data_gas` are zero: [2](#0-1) 

The `TODO(Shahak)` comment acknowledges this is a backward-compatibility workaround for 0.13.2 transactions that omit `l1_data_gas` entirely. However, the condition also fires for new `AllResources` transactions that legitimately carry zero values.

**Step 3 — The consensus P2P path for invoke V3 goes through the broken converter.**

`TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction` dispatches invoke V3 through `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3`: [3](#0-2) 

That converter calls `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` (which uses the broken `ValidResourceBounds::try_from`), then immediately calls `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3`: [4](#0-3) 

**Step 4 — The `RpcInvokeTransactionV3` conversion unconditionally rejects `L1Gas`.**

`TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` explicitly errors on any non-`AllResources` variant: [5](#0-4) 

The comment on line 130 of `rpc_transaction.rs` confirms the expectation: *"This conversion can fail only if the resource_bounds are not AllResources."* The code assumes the protobuf round-trip preserves the `AllResources` variant, but it does not when both gas fields are zero.

**Step 5 — The gateway path is unaffected, creating a split-brain.**

The gateway converts `RpcInvokeTransactionV3` directly to `InternalRpcInvokeTransactionV3`, which stores `AllResourceBounds` (not `ValidResourceBounds`) and always hashes with the three-resource preimage: [6](#0-5) 

The hash function `get_invoke_transaction_v3_hash` includes `L1_DATA_GAS` in the preimage only for `AllResources`: [7](#0-6) 

So the gateway computes `H(…, L1_DATA_GAS_zero)` while a node that successfully reconstructs the transaction from protobuf (if the conversion were to succeed) would compute `H(…)` without `L1_DATA_GAS_zero` — a different hash. The conversion failure in step 4 prevents this hash divergence from being observed, but it also prevents the transaction from being sequenced at all.

---

### Impact Explanation

A valid V3 invoke transaction with `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` is accepted by the gateway and enters the mempool. When the proposing consensus node includes it in a block proposal and broadcasts the `ConsensusTransaction` over P2P, every receiving node fails to deserialize it (`DEPRECATED_RESOURCE_BOUNDS_ERROR`). The block proposal is rejected, the transaction is never sequenced, and the proposing node may be penalized or stalled depending on consensus error handling. This matches:

- **High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload** — the P2P converter binds the wrong type (`L1Gas` instead of `AllResources`) for the executable payload, causing hard rejection.
- **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing** — a transaction accepted by the gateway is silently dropped before sequencing.

---

### Likelihood Explanation

Any user who submits a V3 invoke transaction with zero L2 gas and zero L1 data gas bounds triggers this path. This is a valid and natural configuration for transactions that only consume L1 gas (e.g., simple token transfers on networks where L2 gas is not yet priced). The condition is user-controllable and requires no special privileges.

---

### Recommendation

Replace the zero-value heuristic in `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` with an explicit discriminator. The simplest fix is to always produce `AllResources` when `l1_data_gas` is present in the protobuf message (even if zero), and only fall back to `L1Gas` when `l1_data_gas` is absent (`None` before `unwrap_or_default`):

```rust
// Before unwrap_or_default, check presence:
if value.l1_data_gas.is_none() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
}
```

This preserves backward compatibility with 0.13.2 transactions (which genuinely omit `l1_data_gas`) while correctly handling new transactions that carry explicit zero values. The `TODO(Shahak)` comment should be resolved by asserting `l1_data_gas` is non-`None` once 0.13.2 support is dropped, at which point the heuristic can be removed entirely.

---

### Proof of Concept

```
1. Submit via RPC gateway:
   RpcInvokeTransactionV3 {
       resource_bounds: AllResourceBounds {
           l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
           l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
           l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
       },
       ...
   }

2. Gateway accepts → InternalRpcInvokeTransactionV3 with AllResourceBounds.
   Hash = Poseidon(INVOKE, version, sender, H(tip, L1_GAS_packed, L2_GAS_zero, L1_DATA_GAS_zero), ...)

3. Transaction enters mempool. Proposer builds block, serializes to protobuf::InvokeV3WithProof:
   resource_bounds = { l1_gas: {1000,1}, l2_gas: {0,0}, l1_data_gas: {0,0} }

4. Receiving consensus node deserializes:
   ValidResourceBounds::try_from(resource_bounds)
   → l1_data_gas.is_zero() && l2_gas.is_zero() == true
   → ValidResourceBounds::L1Gas(l1_gas)   ← WRONG

5. TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3:
   match value.resource_bounds {
       ValidResourceBounds::AllResources(_) => ...,  // not taken
       _ => return Err(OutOfRange { "resource_bounds" })  // FIRES
   }
   → mapped to DEPRECATED_RESOURCE_BOUNDS_ERROR

6. TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction returns Err.
   Block proposal rejected. Transaction never sequenced.
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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L1027-1052)
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L697-714)
```rust
impl From<RpcInvokeTransactionV3> for InternalRpcInvokeTransactionV3 {
    fn from(tx: RpcInvokeTransactionV3) -> Self {
        Self {
            sender_address: tx.sender_address,
            calldata: tx.calldata,
            signature: tx.signature,
            nonce: tx.nonce,
            resource_bounds: tx.resource_bounds,
            tip: tx.tip,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            proof_facts: tx.proof_facts,
            // Note: proof field is dropped
        }
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

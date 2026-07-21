### Title
Non-Canonical `ValidResourceBounds` Protobuf Deserialization Produces Wrong Transaction Hash for `AllResources` Transactions with Zero L2/Data Gas — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` silently collapses an `AllResources` variant with zero `l2_gas` and `l1_data_gas` into the `L1Gas` variant. Because `get_tip_resource_bounds_hash` includes the `l1_data_gas` element in the hash preimage only for `AllResources`, the same on-wire transaction data produces two different transaction hashes depending on which path computed it: the gateway (always `AllResources`) versus any node that received the transaction via P2P protobuf (now `L1Gas`). This is the direct sequencer analog of the external report's "value set before an operation, not reset/preserved afterward, causing a subsequent assertion to fail."

---

### Finding Description

**Step 1 — Gateway path always uses `AllResources`.**

`RpcInvokeTransactionV3` stores `resource_bounds: AllResourceBounds` (not `ValidResourceBounds`). [1](#0-0) 

`InternalRpcInvokeTransactionV3` also stores `resource_bounds: AllResourceBounds`. [2](#0-1) 

Both implement `InvokeTransactionV3Trait::resource_bounds()` by wrapping in `ValidResourceBounds::AllResources(...)`, so the hash is always computed as `AllResources`. [3](#0-2) 

**Step 2 — Hash includes `l1_data_gas` only for `AllResources`.**

`get_tip_resource_bounds_hash` appends the `L1_DATA_GAS` element to the poseidon preimage only when the variant is `AllResources`:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
``` [4](#0-3) 

So `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` hashes as `poseidon(tip, l1_gas_felt, zero_l2_felt, zero_l1data_felt)`, while `L1Gas(X)` hashes as `poseidon(tip, l1_gas_felt, zero_l2_felt)`. These are distinct Poseidon outputs. [5](#0-4) 

**Step 3 — Protobuf deserialization destroys the variant.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in the P2P converter:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant changed
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [6](#0-5) 

A transaction submitted via RPC with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is serialized to protobuf with all-zero `l2_gas`/`l1_data_gas` fields. Upon deserialization the converter produces `ValidResourceBounds::L1Gas(X)`. Any subsequent hash computation from this deserialized form yields a hash that differs from the one stored in `InternalRpcTransaction::tx_hash` (computed at the gateway as `AllResources`).

**Step 4 — The invariant is broken.**

The gateway stores `tx_hash = H_AllResources`. A P2P peer deserializes the same transaction bytes and, if it recomputes the hash (e.g., for mempool admission or block verification), obtains `H_L1Gas ≠ H_AllResources`. The transaction is either rejected as having a bad hash, or accepted and stored under the wrong hash, corrupting the receipt/event/state lookup keyed on that hash.

---

### Impact Explanation

This is a **transaction conversion boundary** issue: the same transaction data produces two different canonical hashes depending on the deserialization path. Any node that receives the transaction via P2P and recomputes the hash will bind the wrong hash to the transaction. This matches:

> **High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

Concretely: valid transactions with zero `l2_gas`/`l1_data_gas` are silently rejected by P2P peers (hash mismatch), or stored under an incorrect hash that breaks receipt/event/storage lookups.

---

### Likelihood Explanation

- Any unprivileged user can submit an invoke V3 transaction with `l2_gas = 0` and `l1_data_gas = 0` via the public RPC endpoint. No special privilege is required.
- The `RpcInvokeTransactionV3` struct imposes no lower bound on these fields.
- The protobuf conversion is on the hot path for all P2P-propagated transactions.
- The `TODO` comment in the converter (`// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2`) confirms this branch is intentionally kept alive for backward compatibility, meaning it will remain reachable.

---

### Recommendation

The protobuf deserializer must not use the zero-value heuristic to infer the `ValidResourceBounds` variant. Instead, the protobuf schema should carry an explicit discriminant field (e.g., a `bounds_type` enum: `L1_GAS` vs `ALL_RESOURCES`) so the variant is preserved across serialization. Until the schema is updated, the deserializer should default to `AllResources` for any transaction that arrives with a version ≥ 0.13.3, preserving the extra `l1_data_gas` element in the hash preimage even when its value is zero.

---

### Proof of Concept

1. Submit an invoke V3 transaction via RPC with:
   ```json
   "resource_bounds": {
     "l1_gas": { "max_amount": "0x1000", "max_price_per_unit": "0x1" },
     "l2_gas": { "max_amount": "0x0",    "max_price_per_unit": "0x0" },
     "l1_data_gas": { "max_amount": "0x0", "max_price_per_unit": "0x0" }
   }
   ```
2. The gateway computes `tx_hash = H1` using `ValidResourceBounds::AllResources(...)` → `get_tip_resource_bounds_hash` appends `zero_l1_data_gas_felt` → poseidon over 4 elements.
3. The transaction is propagated to a P2P peer via protobuf. The peer deserializes `ResourceBounds` and, because `l2_gas.is_zero() && l1_data_gas.is_zero()`, produces `ValidResourceBounds::L1Gas(l1_gas)`.
4. The peer recomputes the hash using `L1Gas` → `get_tip_resource_bounds_hash` does NOT append `l1_data_gas_felt` → poseidon over 3 elements → `H2 ≠ H1`.
5. The peer rejects the transaction (hash mismatch) or stores it under `H2`, diverging from the gateway's `H1`.

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L550-566)
```rust
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, SizeOf)]
pub struct RpcInvokeTransactionV3 {
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub resource_bounds: AllResourceBounds,
    pub tip: Tip,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
    #[serde(default, skip_serializing_if = "Proof::is_empty")]
    pub proof: Proof,
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L615-628)
```rust
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, SizeOf)]
pub struct InternalRpcInvokeTransactionV3 {
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub resource_bounds: AllResourceBounds,
    pub tip: Tip,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    pub proof_facts: ProofFacts,
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
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

**File:** crates/starknet_api/src/transaction_hash.rs (L370-405)
```rust
pub(crate) fn get_invoke_transaction_v3_hash<T: InvokeTransactionV3Trait>(
    transaction: &T,
    chain_id: &ChainId,
    transaction_version: &TransactionVersion,
) -> Result<TransactionHash, StarknetApiError> {
    let tip_resource_bounds_hash =
        get_tip_resource_bounds_hash(&transaction.resource_bounds(), transaction.tip())?;
    let paymaster_data_hash =
        HashChain::new().chain_iter(transaction.paymaster_data().0.iter()).get_poseidon_hash();
    let data_availability_mode = concat_data_availability_mode(
        transaction.nonce_data_availability_mode(),
        transaction.fee_data_availability_mode(),
    );
    let account_deployment_data_hash = HashChain::new()
        .chain_iter(transaction.account_deployment_data().0.iter())
        .get_poseidon_hash();
    let calldata_hash =
        HashChain::new().chain_iter(transaction.calldata().0.iter()).get_poseidon_hash();
    let mut hash_chain = HashChain::new()
        .chain(&INVOKE)
        .chain(&transaction_version.0)
        .chain(transaction.sender_address().0.key())
        .chain(&tip_resource_bounds_hash)
        .chain(&paymaster_data_hash)
        .chain(&Felt::try_from(chain_id)?)
        .chain(&transaction.nonce().0)
        .chain(&data_availability_mode)
        .chain(&account_deployment_data_hash)
        .chain(&calldata_hash);
    if !transaction.proof_facts().0.is_empty() {
        let proof_facts_hash =
            HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
        hash_chain = hash_chain.chain(&proof_facts_hash);
    }
    Ok(TransactionHash(hash_chain.get_poseidon_hash()))
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

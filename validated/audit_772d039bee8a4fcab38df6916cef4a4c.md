### Title
P2P Protobuf `ValidResourceBounds` Variant Collapse Produces Non-Canonical Transaction Hash Across Nodes — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ResourceBounds` silently collapses an `AllResources` variant into `L1Gas` whenever `l1_data_gas.is_zero() && l2_gas.is_zero()`. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` element in the Poseidon preimage only for `AllResources`, the same transaction body produces two distinct `TransactionHash` values depending on which code path computed it. The originating node (gateway) always hashes as `AllResources`; any peer that receives the transaction over the mempool P2P channel and reconstructs the hash from the deserialized body hashes as `L1Gas`. The two hashes diverge, breaking the canonical-hash invariant that the entire sequencer pipeline depends on.

---

### Finding Description

**Step 1 – Hash function branches on variant**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` builds the resource-bounds preimage conditionally:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // L1_DATA_GAS omitted
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // included
    }
});
``` [1](#0-0) 

For `AllResources` the Poseidon hash covers `[tip, L1_GAS, L2_GAS, L1_DATA_GAS]`; for `L1Gas` it covers only `[tip, L1_GAS, L2_GAS]`. These are structurally different hash inputs and produce different field elements even when `l1_data_gas` is numerically zero.

**Step 2 – Gateway always hashes as `AllResources`**

`RpcInvokeTransactionV3` carries `AllResourceBounds` (not `ValidResourceBounds`), so the gateway's `convert_rpc_tx_to_internal` always calls `InternalRpcTransactionWithoutTxHash::calculate_transaction_hash` with `ValidResourceBounds::AllResources(...)`. The resulting `tx_hash` is stored in `InternalRpcTransaction`. [2](#0-1) 

**Step 3 – Protobuf round-trip collapses the variant**

When the transaction is propagated over the mempool P2P channel it is serialised to `protobuf::MempoolTransaction` (with `transaction_hash: None`) and deserialised on the peer. The deserialiser applies:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [3](#0-2) 

A transaction submitted with `AllResourceBounds { l1_data_gas: zero, l2_gas: zero }` satisfies the condition and is reconstructed as `L1Gas` on the peer.

**Step 4 – Peer recomputes a different hash**

Because `transaction_hash` is `None` in the protobuf wire format, the receiving node recomputes the hash from the deserialized body. With the body now carrying `L1Gas`, `get_tip_resource_bounds_hash` omits `L1_DATA_GAS` from the preimage, yielding hash `H'` ≠ `H` (the hash the originating node stored). [4](#0-3) 

**Step 5 – Divergence propagates into consensus**

The proposer references the transaction by hash `H` in the block proposal. Validators that received the transaction over P2P stored it under `H'`. They cannot locate the transaction, causing block rejection or consensus stall.

---

### Impact Explanation

This matches the allowed impact: **"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."** The same signed transaction body is bound to two different `TransactionHash` values depending on the code path, breaking the canonical-hash invariant that the mempool, batcher, and consensus all rely on. A proposer and its validators will disagree on whether a transaction is present in the mempool, causing block rejection.

---

### Likelihood Explanation

Any user can submit an `InvokeV3` transaction with `l1_data_gas = {max_amount: 0, max_price_per_unit: 0}` and `l2_gas = {max_amount: 0, max_price_per_unit: 0}`. The gateway's stateless validator does not enforce non-zero gas bounds at admission time. The protobuf collapse is deterministic and requires no privileged access.

---

### Recommendation

Remove the heuristic variant collapse in the protobuf deserialiser. Instead, add an explicit discriminant field to the `ResourceBounds` protobuf message (e.g., a boolean `is_all_resources`) so the variant is preserved faithfully across the wire. Alternatively, always deserialise as `AllResources` and let downstream code normalise. Additionally, include the computed `transaction_hash` in the `MempoolTransaction` protobuf and verify it on receipt to detect any future divergence early.

---

### Proof of Concept

1. Construct an `RpcInvokeTransactionV3` with:
   - `resource_bounds: AllResourceBounds { l1_gas: <any>, l2_gas: {0,0}, l1_data_gas: {0,0} }`
2. Submit to the gateway. The gateway calls `get_invoke_transaction_v3_hash` via `InternalRpcTransactionWithoutTxHash::calculate_transaction_hash`. Because `resource_bounds()` returns `ValidResourceBounds::AllResources(...)`, `get_tip_resource_bounds_hash` appends `L1_DATA_GAS` to the preimage → hash `H`.
3. The gateway propagates the transaction over P2P as `protobuf::MempoolTransaction { transaction_hash: None, txn: InvokeV3(...) }`.
4. A peer deserialises the `ResourceBounds` protobuf. `l1_data_gas.is_zero() && l2_gas.is_zero()` is true → `ValidResourceBounds::L1Gas(l1_gas)`.
5. The peer calls `get_invoke_transaction_v3_hash` on the reconstructed body. `get_tip_resource_bounds_hash` omits `L1_DATA_GAS` → hash `H'` ≠ `H`.
6. The peer stores the transaction under `H'`. When the proposer includes the transaction under `H` in a block, the peer cannot find it and rejects the block. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L203-208)
```rust
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });
```

**File:** crates/starknet_api/src/transaction_hash.rs (L370-404)
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
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L50-75)
```rust
impl From<RpcTransaction> for protobuf::MempoolTransaction {
    fn from(value: RpcTransaction) -> Self {
        match value {
            RpcTransaction::Declare(RpcDeclareTransaction::V3(txn)) => {
                protobuf::MempoolTransaction {
                    txn: Some(protobuf::mempool_transaction::Txn::DeclareV3(txn.into())),
                    // TODO(alonl): Consider removing transaction hash from protobuf
                    transaction_hash: None,
                }
            }
            RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(txn)) => {
                protobuf::MempoolTransaction {
                    txn: Some(protobuf::mempool_transaction::Txn::DeployAccountV3(txn.into())),
                    // TODO(alonl): Consider removing transaction hash from protobuf
                    transaction_hash: None,
                }
            }
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(txn)) => {
                protobuf::MempoolTransaction {
                    txn: Some(protobuf::mempool_transaction::Txn::InvokeV3(txn.into())),
                    // TODO(alonl): Consider removing transaction hash from protobuf
                    transaction_hash: None,
                }
            }
        }
    }
```

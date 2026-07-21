### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing Wrong Transaction Hash After P2P Round-Trip — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` applies a value-based heuristic: if both `l2_gas` and `l1_data_gas` are zero it silently emits `ValidResourceBounds::L1Gas` instead of `ValidResourceBounds::AllResources`. Because `get_tip_resource_bounds_hash` hashes a **different number of field elements** for the two variants, any V3 invoke transaction that was admitted with `AllResources{l2_gas=0, l1_data_gas=0}` will produce a different `tip_resource_bounds_hash` — and therefore a different `TransactionHash` — after a protobuf round-trip. This is the direct sequencer analog of the StableSwap convergence bug: an iterative/conversion process that silently returns a wrong value instead of signalling a canonicalization failure.

---

### Finding Description

**Heuristic in protobuf deserialization** (`crates/apollo_protobuf/src/converters/transaction.rs`, lines 417–436):

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        ...
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)          // ← silently downgrades
        } else {
            ValidResourceBounds::AllResources(...)
        })
    }
}
```

The identical heuristic exists in the RPC layer (`crates/apollo_rpc/src/v0_8/transaction.rs`, lines 188–199).

**Hash divergence in `get_tip_resource_bounds_hash`** (`crates/starknet_api/src/transaction_hash.rs`, lines 188–210):

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 elements total
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 elements
    }
});
Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
```

For `L1Gas`: `Poseidon(tip, L1_GAS_packed, L2_GAS_packed=0)` — **2 resource elements**.  
For `AllResources{l2_gas=0, l1_data_gas=0}`: `Poseidon(tip, L1_GAS_packed, L2_GAS_packed=0, L1_DATA_GAS_packed=0)` — **3 resource elements**.

Poseidon is sensitive to input length; these produce distinct field elements. The resulting `TransactionHash` values diverge.

**Gateway always admits with `AllResources`**: `InternalRpcInvokeTransactionV3` hard-codes `ValidResourceBounds::AllResources(self.resource_bounds)` in its `InvokeTransactionV3Trait` impl (`crates/starknet_api/src/rpc_transaction.rs`, lines 636–638). A transaction with only `l1_gas` non-zero is explicitly accepted by the gateway validator (test case `valid_l1_gas`). So the gateway computes hash H_all using the 3-element Poseidon.

**P2P / storage path uses `InvokeTransactionV3`** which carries `ValidResourceBounds`. After protobuf serialization and deserialization the heuristic fires, the variant becomes `L1Gas`, and any subsequent call to `calculate_transaction_hash` (e.g. during mempool P2P admission, consensus block validation, or `validate_transaction_hash`) produces H_l1 ≠ H_all.

---

### Impact Explanation

A V3 invoke transaction with `AllResourceBounds{l1_gas=X, l2_gas=0, l1_data_gas=0}` is admitted by the gateway with hash H_all. After one protobuf round-trip (P2P mempool propagation or consensus block dissemination), the receiving node reconstructs the transaction with `L1Gas` bounds and computes H_l1 ≠ H_all. Any hash-verification step on the receiving side will either:

- **Reject the transaction** as having an invalid hash → valid transactions are silently dropped before sequencing (High: mempool/gateway admission rejects valid transactions), or  
- **Store the transaction under H_l1** → the sequenced block records the wrong transaction hash, producing a wrong receipt, wrong event, and wrong state commitment (Critical: wrong state/receipt from blockifier/execution logic).

---

### Likelihood Explanation

The trigger is a standard, gateway-accepted V3 invoke transaction with only `l1_gas` non-zero. The gateway validator explicitly permits this (test `valid_l1_gas`). No privileged access is required. The heuristic fires on every protobuf round-trip for such transactions, making this deterministically reproducible.

---

### Recommendation

Remove the value-based heuristic from `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. The protobuf wire format already carries all three resource-bound fields; the variant should be determined by the **protocol version** or an explicit tag, not by whether the values happen to be zero. For the transition period, the `TODO(Shahak)` comment already acknowledges this: once 0.13.2 support is dropped, assert that `l1_data_gas` is never `None` and always deserialize as `AllResources` for V3 transactions. The same fix applies to `From<ResourceBoundsMapping> for ValidResourceBounds` in the RPC layer.

---

### Proof of Concept

```
1. Submit InvokeV3 with AllResourceBounds { l1_gas: {max_amount:1, max_price:1},
                                             l2_gas: {0,0}, l1_data_gas: {0,0} }
   → gateway computes H_all = Poseidon("invoke", ver, addr,
       Poseidon(tip, pack(L1_GAS,1,1), pack(L2_GAS,0,0), pack(L1_DATA,0,0)),
       ...)

2. Transaction stored in mempool with hash H_all.

3. Transaction serialized to protobuf::InvokeV3 (resource_bounds has l2_gas=0, l1_data_gas=0).

4. Receiving node calls TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
   l1_data_gas.is_zero() && l2_gas.is_zero() → true → emits L1Gas(l1_gas)

5. Receiving node calls calculate_transaction_hash:
   H_l1 = Poseidon("invoke", ver, addr,
       Poseidon(tip, pack(L1_GAS,1,1), pack(L2_GAS,0,0)),   ← only 2 resource elements
       ...)

6. H_l1 ≠ H_all  →  hash mismatch; transaction rejected or stored under wrong hash.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_rpc/src/v0_8/transaction.rs (L188-199)
```rust
impl From<ResourceBoundsMapping> for ValidResourceBounds {
    fn from(value: ResourceBoundsMapping) -> Self {
        if value.l1_data_gas.is_zero() && value.l2_gas.is_zero() {
            Self::L1Gas(value.l1_gas)
        } else {
            Self::AllResources(AllResourceBounds {
                l1_gas: value.l1_gas,
                l1_data_gas: value.l1_data_gas,
                l2_gas: value.l2_gas,
            })
        }
    }
```

**File:** crates/starknet_api/src/transaction_hash.rs (L188-210)
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-638)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
```

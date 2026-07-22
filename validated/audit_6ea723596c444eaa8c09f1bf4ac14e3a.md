### Title
`ValidResourceBounds` protobuf deserializer silently downgrades `AllResources` to `L1Gas` when l2/l1_data gas are zero, producing a wrong transaction hash across the P2P block-sync boundary — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` applies a zero-value heuristic to distinguish pre-0.13.3 (`L1Gas`) from post-0.13.3 (`AllResources`) V3 transactions. When both `l2_gas` and `l1_data_gas` are zero, it silently returns `ValidResourceBounds::L1Gas` instead of `ValidResourceBounds::AllResources`. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` field in the hash only for `AllResources`, a V3 transaction signed with the `AllResources` hash (which includes `L1_DATA_GAS = 0`) will have its hash recomputed as the shorter `L1Gas` hash (which omits `L1_DATA_GAS`) after any protobuf round-trip. The two hashes are distinct Poseidon values. Any node that re-derives the transaction hash from the deserialized protobuf — as happens during P2P block sync — will compute the wrong hash, causing valid blocks to be rejected or transactions to be stored and served with an incorrect hash.

---

### Finding Description

**Root cause — `crates/apollo_protobuf/src/converters/transaction.rs` lines 417–436:**

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        ...
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)          // ← wrong branch for post-0.13.3 txs
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
}
```

The serializer (`From<ValidResourceBounds> for protobuf::ResourceBounds`, lines 471–489) always emits `l1_data_gas: Some(zero_limits)` for an `AllResources` transaction with zero data-gas bounds. The deserializer then sees `l1_data_gas = Some(zero)`, calls `unwrap_or_default()` to get a zero `ResourceBounds`, and because both `l2_gas.is_zero() && l1_data_gas.is_zero()` it returns `L1Gas` — discarding the `AllResources` variant information.

**Hash domain divergence — `crates/starknet_api/src/transaction_hash.rs` lines 188–211:**

```rust
pub fn get_tip_resource_bounds_hash(
    resource_bounds: &ValidResourceBounds,
    tip: &Tip,
) -> Result<Felt, StarknetApiError> {
    ...
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],                          // no L1_DATA_GAS
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // includes it
        }
    });
    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
```

For a V3 transaction with `AllResources { l1_gas: {A, P}, l2_gas: {0,0}, l1_data_gas: {0,0} }`:

| Variant after deserialization | Hash preimage |
|---|---|
| `AllResources` (original) | `poseidon(tip, concat(L1_GAS,A,P), concat(L2_GAS,0,0), concat(L1_DATA_GAS,0,0))` |
| `L1Gas` (after protobuf round-trip) | `poseidon(tip, concat(L1_GAS,A,P), concat(L2_GAS,0,0))` |

These are distinct Poseidon outputs → **H_all ≠ H_l1**.

**Trigger path:**

1. A user submits a V3 invoke transaction with `AllResourceBounds { l1_gas: {non-zero}, l2_gas: {0,0}, l1_data_gas: {0,0} }` to the gateway. The gateway's `validate_resource_bounds` check passes because `max_possible_fee(Tip::ZERO) = l1_gas.max_amount * l1_gas.max_price_per_unit > 0`.
2. The gateway computes H_all (the `AllResources` hash) and stores `InternalRpcTransaction { tx_hash: H_all, ... }`.
3. The proposer includes the transaction in a block; the block's transaction list records H_all.
4. A syncing node receives the block via P2P. The transaction is deserialized using `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` → `L1Gas`.
5. The syncing node recomputes the hash as H_l1 ≠ H_all.
6. `validate_transaction_hash` returns `false`; the block is rejected as invalid. Alternatively, if hash validation is skipped, the transaction is stored and served with the wrong hash H_l1.

---

### Impact Explanation

**Wrong state / wrong hash served by RPC (Critical/High):** If the syncing node skips hash validation and stores the transaction with H_l1, any subsequent `starknet_getTransactionByHash(H_all)` call returns nothing (the transaction is indexed under H_l1), and `starknet_getTransactionByHash(H_l1)` returns a transaction whose hash field does not match the block's transaction commitment. The `get_execution_info` syscall inside the account contract would also receive H_l1 instead of H_all, causing the account's `__validate__` signature check to fail and the transaction to revert — a different execution result than the proposer produced.

**Block rejection (High):** If the syncing node does validate transaction hashes, it rejects every block that contains a V3 transaction with zero l2/l1_data gas bounds, causing permanent state-sync failure for those blocks.

---

### Likelihood Explanation

Any unprivileged user can craft and submit a valid V3 invoke transaction with zero l2_gas and l1_data_gas through the normal gateway endpoint. The protobuf round-trip occurs on every P2P block-sync message. No special network position or privileged access is required.

---

### Recommendation

In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` (`crates/apollo_protobuf/src/converters/transaction.rs`), do not use the zero-value heuristic to infer the resource-bounds variant. Instead:

- Return `AllResources` whenever `l1_data_gas` is `Some(...)` (even if zero), and return `L1Gas` only when `l1_data_gas` is `None` (absent from the wire message — the pre-0.13.3 case).

```rust
let l1_data_gas_opt = value.l1_data_gas;
let l1_gas: ResourceBounds = l1_gas.try_into()?;
let l2_gas: ResourceBounds = l2_gas.try_into()?;
Ok(match l1_data_gas_opt {
    None => ValidResourceBounds::L1Gas(l1_gas),   // pre-0.13.3: field absent
    Some(l1_data_gas) => {
        let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
        ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
    }
})
```

This preserves backward compatibility (pre-0.13.3 messages omit `l1_data_gas`) while correctly handling post-0.13.3 messages that happen to carry zero data-gas bounds.

---

### Proof of Concept

```
1. Craft RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     }
   Sign it over H_all = get_invoke_transaction_v3_hash(AllResources{...}).

2. Submit to gateway → accepted (max_possible_fee = 1000 > 0).
   Gateway stores tx_hash = H_all.

3. Proposer includes tx in block B; block header commits H_all.

4. Syncing node receives B via P2P.
   Protobuf wire: ResourceBounds { l1_gas: Some({1000,1}), l2_gas: Some({0,0}), l1_data_gas: Some({0,0}) }
   Deserializer: l1_data_gas.is_zero() && l2_gas.is_zero() → ValidResourceBounds::L1Gas({1000,1})
   Recomputed hash: H_l1 = get_invoke_transaction_v3_hash(L1Gas{...})

5. H_l1 ≠ H_all  (L1_DATA_GAS=0 element present in H_all, absent in H_l1).
   validate_transaction_hash returns false → block B rejected.
   OR: tx stored under H_l1; get_execution_info returns H_l1 to __validate__,
       signature check fails, tx reverts (diverges from proposer's execution).
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

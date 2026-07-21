### Title
Protobuf `ValidResourceBounds` Deserialization Silently Collapses `AllResources(l2=0, l1data=0)` to `L1Gas`, Producing a Different Transaction Hash - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf-to-Rust conversion for `ValidResourceBounds` silently reclassifies an `AllResources` transaction whose `l2_gas` and `l1_data_gas` happen to be zero into a `L1Gas` transaction. Because the two variants produce structurally different hash preimages (2 vs 3 resource-bound elements), the transaction hash computed at the gateway diverges from the hash recomputed by any peer that receives the transaction over P2P, breaking the canonical hash invariant.

### Finding Description

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` applies the following classification gate:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

A user submitting a V3 invoke transaction through the RPC uses `RpcInvokeTransactionV3`, whose `resource_bounds` field is typed `AllResourceBounds` (not `ValidResourceBounds`). Setting `l2_gas = 0` and `l1_data_gas = 0` is entirely valid — it means the user is willing to pay zero for those resources. The gateway converts this to `ValidResourceBounds::AllResources` and computes the transaction hash via `get_invoke_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash`. [2](#0-1) 

The hash preimage is built by `valid_resource_bounds_as_felts`:

- For `ValidResourceBounds::AllResources`: emits **three** `ResourceAsFelts` entries — L1Gas, L2Gas, **and** L1DataGas.
- For `ValidResourceBounds::L1Gas`: emits only **two** entries — L1Gas and L2Gas (L1DataGas is absent). [3](#0-2) 

When the same transaction is later serialized to protobuf for P2P propagation (with `transaction_hash: None`) and deserialized by a receiving peer, the `l1_data_gas.is_zero() && l2_gas.is_zero()` gate fires and the peer reconstructs the transaction as `ValidResourceBounds::L1Gas`. The peer then recomputes the hash over only two resource elements, yielding a hash **H2 ≠ H1**. [4](#0-3) 

The `MempoolTransaction` protobuf explicitly sets `transaction_hash: None`, so the receiving node must recompute the hash from the deserialized body — the body that now carries the wrong variant. [5](#0-4) 

The conversion path from `InvokeTransactionV3` (which carries `ValidResourceBounds`) back to `RpcInvokeTransactionV3` (which requires `AllResourceBounds`) already guards against `L1Gas` bounds by returning an error:

```rust
ValidResourceBounds::AllResources(bounds) => bounds,
_ => return Err(StarknetApiError::OutOfRange { ... }),
``` [6](#0-5) 

This means the receiving node cannot even reconstruct the original `RpcInvokeTransactionV3` from the deserialized `InvokeTransactionV3`, so the transaction is either silently dropped or stored under the wrong hash.

### Impact Explanation

A valid V3 invoke transaction with `AllResources(l2_gas=0, l1_data_gas=0)` is accepted by the gateway under hash H1. After P2P propagation, every receiving peer recomputes hash H2 ≠ H1 (or fails to reconstruct the transaction entirely). The transaction is therefore either permanently lost from the mempool of all peers, or stored under a different hash, breaking deduplication, nonce ordering, and block inclusion checks. This matches: **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

### Likelihood Explanation

Any user who submits a V3 transaction and sets `l2_gas.max_amount = 0` and `l1_data_gas.max_amount = 0` (a natural choice when the user only wants to bound L1 gas) triggers this path. No special privilege is required; the RPC accepts the transaction normally. The condition is reachable by any unprivileged sender.

### Recommendation

Remove the heuristic type-narrowing from the protobuf deserializer. The wire format should carry an explicit discriminant (e.g., a boolean `is_all_resources` flag, or a separate oneof) so that the deserializer can reconstruct the exact `ValidResourceBounds` variant the sender signed. Alternatively, always deserialize into `AllResources` when all three resource fields are present in the protobuf message, regardless of whether their values are zero.

### Proof of Concept

1. Construct a V3 invoke transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`.
2. Submit via RPC. The gateway computes hash H1 using `get_invoke_transaction_v3_hash` over three resource elements (L1Gas, L2Gas(0), L1DataGas(0)).
3. The transaction is serialized to `protobuf::MempoolTransaction` with `transaction_hash: None` and propagated to a peer.
4. The peer deserializes via `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`; the `l1_data_gas.is_zero() && l2_gas.is_zero()` branch fires, producing `ValidResourceBounds::L1Gas(X)`.
5. The peer calls `get_invoke_transaction_v3_hash` over two resource elements (L1Gas, L2Gas(0)) — L1DataGas is absent.
6. The peer obtains hash H2 ≠ H1. The transaction is either rejected (signature mismatch against H2) or stored under H2, making it invisible to any lookup by H1.

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L50-75)
```rust
use starknet_types_core::felt::Felt;

use super::common::{
    enum_int_to_volition_domain,
    missing,
    try_from_starkfelt_to_u128,
    try_from_starkfelt_to_u32,
    volition_domain_to_enum_int,
};
use super::ProtobufConversionError;
use crate::sync::{DataOrFin, Query, TransactionQuery};
use crate::transaction::DeclareTransactionV3Common;
use crate::{auto_impl_into_and_try_from_vec_u8, protobuf};

impl TryFrom<protobuf::TransactionsResponse> for DataOrFin<FullTransaction> {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::TransactionsResponse) -> Result<Self, Self::Error> {
        let Some(transaction_message) = value.transaction_message else {
            return Err(missing("TransactionsResponse::transaction_message"));
        };

        match transaction_message {
            protobuf::transactions_response::TransactionMessage::TransactionWithReceipt(
                tx_with_receipt,
            ) => {
                let result: FullTransaction = tx_with_receipt.try_into()?;
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L431-435)
```rust
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
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

**File:** crates/starknet_api/src/transaction/fields.rs (L333-350)
```rust
pub fn valid_resource_bounds_as_felts(
    resource_bounds: &ValidResourceBounds,
    exclude_l1_data_gas: bool,
) -> Result<Vec<ResourceAsFelts>, StarknetApiError> {
    let mut resource_bounds_vec: Vec<_> = vec![
        ResourceAsFelts::new(Resource::L1Gas, &resource_bounds.get_l1_bounds())?,
        ResourceAsFelts::new(Resource::L2Gas, &resource_bounds.get_l2_bounds())?,
    ];
    if exclude_l1_data_gas {
        return Ok(resource_bounds_vec);
    }
    if let ValidResourceBounds::AllResources(AllResourceBounds { l1_data_gas, .. }) =
        resource_bounds
    {
        resource_bounds_vec.push(ResourceAsFelts::new(Resource::L1DataGas, l1_data_gas)?)
    }
    Ok(resource_bounds_vec)
}
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

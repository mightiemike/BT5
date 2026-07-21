### Title
`ValidResourceBounds` Protobuf Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Different Transaction Hash - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` classifies any transaction whose `l2_gas` and `l1_data_gas` fields are both zero as `ValidResourceBounds::L1Gas`, even when the transaction was originally submitted and signed as `ValidResourceBounds::AllResources`. Because `get_tip_resource_bounds_hash` hashes a different number of resource felts for the two variants, the transaction hash computed after a protobuf round-trip diverges from the hash the signer committed to, breaking the canonicalization invariant across the P2P/sync boundary.

### Finding Description

**Deserialization logic** in `crates/apollo_protobuf/src/converters/transaction.rs` lines 417–436:

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        // ...
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // absent → zero
        // ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)          // ← variant downgrade
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
}
``` [1](#0-0) 

A V3 transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is valid at the gateway (the `ZeroResourceBounds` check only requires *at least one* non-zero bound, which `l1_gas = X` satisfies). [2](#0-1) 

After a protobuf round-trip the transaction is reconstructed as `ValidResourceBounds::L1Gas(l1_gas)`.

**Hash divergence** in `crates/starknet_api/src/transaction_hash.rs` lines 188–211:

```rust
pub fn get_tip_resource_bounds_hash(
    resource_bounds: &ValidResourceBounds,
    tip: &Tip,
) -> Result<Felt, StarknetApiError> {
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];
    // For AllResources: appends a third felt for l1_data_gas
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],                          // 2 felts
        ValidResourceBounds::AllResources(all) =>
            vec![get_concat_resource(&all.l1_data_gas, L1_DATA_GAS)?],    // 3 felts
    });
    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
``` [3](#0-2) 

`poseidon_hash([tip, l1_gas, l2_gas=0])` ≠ `poseidon_hash([tip, l1_gas, l2_gas=0, l1_data_gas=0])`. The extra zero felt changes the digest.

The hash is embedded in every V3 invoke hash computation path: [4](#0-3) 

### Impact Explanation

Any component that reconstructs a `Transaction` (starknet_api) from protobuf bytes and then calls `calculate_transaction_hash` on it will obtain a hash that differs from the one the account owner signed. Concretely:

- **P2P block sync**: a syncing node deserializes the transaction as `L1Gas`, recomputes the hash, and stores a different value than the originating sequencer. Subsequent `get_transaction` RPC calls return the wrong hash.
- **`trace_transaction` / `simulate_transactions`**: re-execution passes the wrong hash into the account's `__validate__` entry point; the account's ECDSA check fails, the transaction reverts, and the trace/simulation output is wrong.
- **Starknet OS / proof generation**: the OS recomputes the hash from transaction fields. If it sees `L1Gas` (2 resources) instead of `AllResources` (3 resources), the computed hash mismatches the one in the block commitment, invalidating the proof.

This matches: *"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload"* (High) and *"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value"* (High).

### Likelihood Explanation

The trigger requires a V3 transaction with `l2_gas = 0` and `l1_data_gas = 0`. The gateway's `ZeroResourceBounds` check only rejects transactions where *all three* bounds are zero, so a transaction with only `l1_gas` non-zero passes admission. Such transactions are uncommon in practice (most wallets set non-zero L2 gas since Starknet 0.13.3) but are fully valid and can be crafted by any user.

### Recommendation

Replace the zero-equality heuristic with an explicit version/type tag. The protobuf `ResourceBounds` message should carry a discriminant field (e.g., `is_all_resources: bool`) so the deserializer can reconstruct the exact variant the sender intended, independent of whether the numeric values happen to be zero. Until then, the deserializer should default to `AllResources` for any transaction whose protobuf message was produced by a post-0.13.3 node (identifiable by the presence of the `l1_data_gas` field, even when zero):

```rust
// Treat absent l1_data_gas as L1Gas (pre-0.13.3 compat), but
// treat *present* l1_data_gas=0 as AllResources.
Ok(match value.l1_data_gas {
    None if l2_gas.is_zero() => ValidResourceBounds::L1Gas(l1_gas),
    _ => ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }),
})
```

### Proof of Concept

1. Craft a V3 invoke transaction with `AllResourceBounds { l1_gas: 1000, l2_gas: 0, l1_data_gas: 0 }`.
2. Submit to the gateway; it is accepted (l1_gas is non-zero).
3. The gateway computes `H_all = get_tip_resource_bounds_hash(AllResources{...})` — a 3-felt poseidon hash — and stores `InternalRpcTransaction { tx_hash: H_all, ... }`.
4. Serialize the transaction to protobuf (`protobuf::ResourceBounds { l1_gas: 1000, l2_gas: 0, l1_data_gas: 0 }`).
5. Deserialize on a syncing node: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas(1000)`.
6. Call `calculate_transaction_hash` on the deserialized transaction: `H_l1 = get_tip_resource_bounds_hash(L1Gas{...})` — a 2-felt poseidon hash.
7. Assert `H_all ≠ H_l1` (they differ because the poseidon state absorbs a different number of elements).
8. The syncing node stores `H_l1`; any `trace_transaction(H_all)` lookup fails or re-executes with the wrong hash, causing `__validate__` to revert. [5](#0-4) [6](#0-5)

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

**File:** crates/apollo_gateway/src/errors.rs (L67-71)
```rust
    #[error(
        "At least one resource bound (L1, L2, or L1 Data) must be non-zero. Got:
        {resource_bounds:?}."
    )]
    ZeroResourceBounds { resource_bounds: AllResourceBounds },
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

**File:** crates/starknet_api/src/transaction_hash.rs (L599-630)
```rust
pub(crate) fn get_declare_transaction_v3_hash<T: DeclareTransactionV3Trait>(
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

    Ok(TransactionHash(
        HashChain::new()
            .chain(&DECLARE)
            .chain(&transaction_version.0)
            .chain(transaction.sender_address().0.key())
            .chain(&tip_resource_bounds_hash)
            .chain(&paymaster_data_hash)
            .chain(&Felt::try_from(chain_id)?)
            .chain(&transaction.nonce().0)
            .chain(&data_availability_mode)
            .chain(&account_deployment_data_hash)
            .chain(&transaction.class_hash().0)
            .chain(&transaction.compiled_class_hash().0)
            .get_poseidon_hash(),
    ))
```

### Title
`AllResources(l1_gas, l2_gas=0, l1_data_gas=0)` Silently Collapses to `L1Gas` in Protobuf Deserialization, Producing a Different Transaction Hash - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf-to-`ValidResourceBounds` conversion uses a value-based heuristic to select the enum variant. When a V3 transaction carries `AllResources` with both `l2_gas` and `l1_data_gas` equal to zero, the deserializer silently downgrades it to `L1Gas`. Because `get_tip_resource_bounds_hash` hashes a different number of elements for each variant, the transaction hash computed after P2P deserialization diverges from the hash computed at the originating node, breaking hash-validation of synced blocks.

### Finding Description

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` decides the variant purely from the deserialized field values:

```rust
let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // None → zero
// ...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant chosen by value, not by wire tag
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

Any `AllResources(l1_gas, l2_gas=0, l1_data_gas=0)` value — a perfectly legal post-0.13.3 transaction — is therefore deserialized as `L1Gas(l1_gas)`.

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` hashes a **different number of elements** depending on the variant:

```rust
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];
// For AllResources only — adds a third element:
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
``` [2](#0-1) 

- `AllResources(l1_gas, 0, 0)` → hash over `[tip, L1_GAS, L2_GAS, L1_DATA_GAS]` (4 elements)
- `L1Gas(l1_gas)` → hash over `[tip, L1_GAS, L2_GAS]` (3 elements)

These produce **distinct Poseidon digests** for the same logical transaction.

The `validate_transaction_hash` path recomputes the hash from the deserialized `Transaction` and checks it against the wire-carried hash: [3](#0-2) 

After P2P deserialization the recomputed hash is the `L1Gas` hash; the stored hash is the `AllResources` hash. The check returns `false`, and the block is rejected.

The `ValidResourceBounds` enum documents the semantic distinction explicitly:

```rust
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}
``` [4](#0-3) 

The two variants are **not value-equivalent** even when the numeric fields are identical; they differ in hash domain.

### Impact Explanation

A syncing peer that receives a block containing a V3 transaction with `AllResources(l1_gas, 0, 0)` will recompute a different transaction hash than the sequencer that produced the block. `validate_transaction_hash` will return `false`, causing the peer to reject a valid block. This maps to:

> **Critical — Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input.**

and

> **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

### Likelihood Explanation

The gateway accepts any `AllResourceBounds` with zero `l2_gas` and zero `l1_data_gas` — no validation rejects it. The `AllResourceBounds` struct has a `Default` impl that produces exactly this configuration, and helper constructors such as `new_unlimited_gas_no_fee_enforcement` produce `AllResources` with zero `l1_gas.max_amount` and zero `l1_data_gas`. Any operator or test harness that submits a V3 transaction with default or zero resource bounds can trigger the condition. The P2P block-sync path is always active on a live node, so the divergence is reachable without any privileged access.

### Recommendation

Replace the value-based heuristic with an explicit wire-level discriminant. Two options:

1. **Add a boolean/enum field to `protobuf::ResourceBounds`** (e.g., `all_resources: bool`) that is set by the serializer based on the Rust variant, and read by the deserializer to select the variant unconditionally.

2. **Preserve the variant in the serializer**: when serializing `L1Gas`, set `l1_data_gas = None`; when serializing `AllResources`, always set `l1_data_gas = Some(value)` — even when zero — and change the deserializer to use `Some(_)` vs `None` as the discriminant instead of the numeric value.

Either approach makes the round-trip lossless and eliminates the hash divergence.

### Proof of Concept

1. Submit a V3 invoke transaction to the gateway with `resource_bounds = AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`.
2. The gateway computes `tx_hash_A = poseidon(INVOKE, version, sender, fee_hash_AllResources, ...)` where `fee_hash_AllResources = poseidon(tip, concat(X, L1_GAS), concat(0, L2_GAS), concat(0, L1_DATA_GAS))`.
3. The transaction is included in a block and serialized to protobuf for P2P sync.
4. A syncing peer deserializes `protobuf::ResourceBounds` → `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas(X)`.
5. The peer recomputes `tx_hash_B = poseidon(INVOKE, version, sender, fee_hash_L1Gas, ...)` where `fee_hash_L1Gas = poseidon(tip, concat(X, L1_GAS), concat(0, L2_GAS))` — **missing the `L1_DATA_GAS` element**.
6. `tx_hash_A ≠ tx_hash_B`; `validate_transaction_hash` returns `false`; the peer rejects the block.

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

**File:** crates/starknet_api/src/transaction_hash.rs (L170-185)
```rust
pub fn validate_transaction_hash(
    transaction: &Transaction,
    block_number: &BlockNumber,
    chain_id: &ChainId,
    expected_hash: TransactionHash,
    transaction_options: &TransactionOptions,
) -> Result<bool, StarknetApiError> {
    let mut possible_hashes = get_deprecated_transaction_hashes(
        chain_id,
        block_number,
        transaction,
        transaction_options,
    )?;
    possible_hashes.push(get_transaction_hash(transaction, chain_id, transaction_options)?);
    Ok(possible_hashes.contains(&expected_hash))
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

**File:** crates/starknet_api/src/transaction/fields.rs (L363-367)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}
```

### Title
Protobuf `ValidResourceBounds` Round-Trip Silently Collapses `AllResources` to `L1Gas`, Producing a Different Transaction Hash Preimage - (File: crates/apollo_protobuf/src/converters/transaction.rs)

### Summary

The protobuf deserialization of `ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` silently reinterprets an `AllResources` variant (with zero `l2_gas` and `l1_data_gas`) as `L1Gas`. Because `get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` includes the `L1_DATA_GAS` field in the Poseidon hash preimage only for `AllResources`, the transaction hash computed after a protobuf round-trip differs from the hash computed at the originating node. This is the Sequencer-native analog of the external bug: a data structure is decoded under the wrong format assumption, producing a wrong hash/execution payload.

### Finding Description

**Step 1 – The originating node always uses `AllResources`.**

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds` (not `ValidResourceBounds`). When the gateway converts it to `InternalRpcInvokeTransactionV3`, the `resource_bounds` field is still `AllResourceBounds`. The `InvokeTransactionV3Trait` implementation for `InternalRpcInvokeTransactionV3` wraps it as `ValidResourceBounds::AllResources(self.resource_bounds)`. Therefore `get_invoke_transaction_v3_hash` always calls `get_tip_resource_bounds_hash` with the `AllResources` variant, and the hash preimage always includes the `L1_DATA_GAS` packed felt. [1](#0-0) [2](#0-1) 

**Step 2 – The protobuf serializer always emits `l1_data_gas`.**

When `ValidResourceBounds::AllResources` is serialized to protobuf, `l1_data_gas` is always written, even when it is zero. [3](#0-2) 

**Step 3 – The protobuf deserializer collapses the variant.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `transaction.rs` (used for the `InvokeTransactionV3` storage/P2P-sync type) applies the following decision:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

A transaction originally signed as `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is serialized to protobuf with `l1_data_gas = 0`. On deserialization the condition `l1_data_gas.is_zero() && l2_gas.is_zero()` is true, so the variant is reconstructed as `L1Gas(l1_gas: X)`. [4](#0-3) 

**Step 4 – The hash preimage diverges.**

`get_tip_resource_bounds_hash` branches on the variant:

- `L1Gas` → preimage = `[tip, L1_GAS_packed, L2_GAS_packed]` (2 resource felts)
- `AllResources` → preimage = `[tip, L1_GAS_packed, L2_GAS_packed, L1_DATA_GAS_packed]` (3 resource felts, even when `L1_DATA_GAS_packed` is zero)

The Poseidon hash of a 3-element sequence differs from that of a 2-element sequence even when the third element is zero. Therefore `H_originating ≠ H_after_roundtrip`. [5](#0-4) 

**Step 5 – A valid transaction can trigger this.**

The gateway's stateless validator explicitly accepts `AllResourceBounds { l1_gas: NON_EMPTY, l2_gas: 0, l1_data_gas: 0 }` as a valid transaction. There is no check that prevents zero `l2_gas` and `l1_data_gas` from being submitted. [6](#0-5) 

### Impact Explanation

When a block containing such a transaction is propagated over P2P sync, the receiving node deserializes the transaction as `L1Gas` and recomputes a hash that differs from the one stored in the block body. Any component that recomputes and compares the hash (e.g., `validate_transaction_hash`, block-hash commitment verification, or an account contract's `__validate__` entry point which hashes the transaction fields it receives via `get_execution_info`) will observe a mismatch. Concretely:

- **Wrong transaction hash bound**: the hash used by the blockifier for signature verification differs from the hash the user signed, causing `__validate__` to revert for an otherwise-valid transaction.
- **Wrong RPC response**: an RPC node that reads the deserialized `L1Gas` variant and recomputes the hash returns an authoritative-looking wrong hash to clients.

Both match the allowed High-impact scope: *"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload"* and *"RPC execution … returns an authoritative-looking wrong value."*

### Likelihood Explanation

Any user who submits a V3 invoke transaction with non-zero `l1_gas` and zero `l2_gas`/`l1_data_gas` (a common pattern for users who only care about L1 gas) triggers this path. The gateway accepts such transactions without restriction. The conversion bug is exercised on every P2P sync of a block containing such a transaction.

### Recommendation

Fix the protobuf deserializer to preserve the `AllResources` variant whenever `l1_data_gas` is explicitly present in the protobuf message, regardless of its value. One approach:

```rust
// In TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
if value.l1_data_gas.is_none() && l2_gas.is_zero() {
    Ok(ValidResourceBounds::L1Gas(l1_gas))
} else {
    Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))
}
```

This preserves the `AllResources` variant when `l1_data_gas` was explicitly serialized (even as zero), matching the originating node's hash computation. The `L1Gas` path is only taken when `l1_data_gas` is truly absent (legacy 0.13.2 messages).

### Proof of Concept

1. Submit a V3 invoke transaction to the gateway with `resource_bounds = AllResourceBounds { l1_gas: 1000, l2_gas: 0, l1_data_gas: 0 }`.
2. The gateway computes hash H1 via `get_invoke_transaction_v3_hash` → `get_tip_resource_bounds_hash(AllResources(...))` → 3-element Poseidon preimage.
3. The transaction is included in a block. Serialize the block body to protobuf (P2P sync format).
4. Deserialize the block body on a second node. The `ValidResourceBounds` for this transaction is reconstructed as `L1Gas(1000)` by `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`.
5. Recompute the hash on the second node via `get_invoke_transaction_v3_hash` → `get_tip_resource_bounds_hash(L1Gas(...))` → 2-element Poseidon preimage → hash H2.
6. Assert `H1 ≠ H2`. The second node now holds the transaction under the wrong hash, and any signature verification against H2 will fail for a user who signed against H1. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L471-490)
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

**File:** crates/starknet_api/src/transaction/fields.rs (L363-366)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
```

### Title
`ValidResourceBounds::AllResources` with zero L2/L1DataGas silently collapses to `ValidResourceBounds::L1Gas` in protobuf deserialization, producing a divergent transaction hash - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a zero-value heuristic to decide between the `L1Gas` and `AllResources` variants. A V3 transaction submitted with `AllResources` bounds where `l2_gas` and `l1_data_gas` are both zero (a configuration the gateway explicitly accepts) is serialized to protobuf bytes that are indistinguishable from a `L1Gas` transaction. On deserialization the variant flips to `L1Gas`. Because `get_tip_resource_bounds_hash` hashes a different number of resource-bound felts for each variant, the reconstructed transaction hash diverges from the hash the signer and the primary sequencer computed, breaking signature verification and block-hash commitment validation on every syncing node.

### Finding Description

**Serialization path** (`From<ValidResourceBounds> for protobuf::ResourceBounds`, `crates/apollo_protobuf/src/converters/transaction.rs` lines 471-490):

```rust
ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
    l1_gas: Some(l1_gas.into()),
    l2_gas: Some(value.get_l2_bounds().into()),   // ResourceBounds::default() → all-zero
    l1_data_gas: Some(ResourceBounds::default().into()), // all-zero
},
ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
    protobuf::ResourceBounds {
        l1_gas: Some(l1_gas.into()),
        l2_gas: Some(l2_gas.into()),
        l1_data_gas: Some(l1_data_gas.into()),
    },
```

When `AllResources` has `l2_gas = 0` and `l1_data_gas = 0`, the serialized bytes are **byte-for-byte identical** to those of a `L1Gas` transaction.

**Deserialization path** (`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, lines 417-436):

```rust
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
// ...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant flips here
} else {
    ValidResourceBounds::AllResources(...)
})
```

The heuristic cannot distinguish the two cases and always produces `L1Gas`.

**Hash divergence** (`get_tip_resource_bounds_hash`, `crates/starknet_api/src/transaction_hash.rs` lines 188-211):

```rust
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 felts total
    ValidResourceBounds::AllResources(all) =>
        vec![get_concat_resource(&all.l1_data_gas, L1_DATA_GAS)?],   // 3 felts total
});
Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
```

`AllResources` always chains an extra `L1_DATA_GAS` felt (even when it is zero). The Poseidon hash of `[tip, L1_GAS, L2_GAS_zero]` ≠ `[tip, L1_GAS, L2_GAS_zero, L1_DATA_GAS_zero]`. The two variants therefore produce **different transaction hashes** for the same logical transaction.

**Gateway acceptance of the triggering configuration** (`crates/apollo_gateway/src/stateless_transaction_validator_test.rs` lines 70-82):

```rust
#[case::valid_l1_gas(
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l1_gas: NON_EMPTY_RESOURCE_BOUNDS,
            ..Default::default()   // l2_gas = 0, l1_data_gas = 0
        },
        ..Default::default()
    }
)]
```

The gateway's `ZeroResourceBounds` check only fires when **all three** bounds are zero; a transaction with non-zero `l1_gas` and zero `l2_gas`/`l1_data_gas` passes stateless validation.

### Impact Explanation

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter is used in the **block-sync path** when a node deserializes historical `InvokeTransactionV3` / `DeclareTransactionV3` / `DeployAccountTransactionV3` objects received from peers. The reconstructed `ValidResourceBounds::L1Gas` variant causes `get_invoke_transaction_v3_hash` (and its declare/deploy-account equivalents) to compute a hash that omits the `L1_DATA_GAS` felt. This diverges from:

1. The hash the signer committed to (signed with `AllResources` hash).
2. The hash the primary sequencer stored in the block's `transaction_commitment` Merkle tree.

Consequences:
- **Block-hash commitment mismatch**: The syncing node recomputes the transaction commitment from the wrong per-transaction hashes; the resulting root does not match the committed `transaction_commitment` in the block header, so the block is rejected. Valid blocks containing such transactions cannot be synced.
- **Signature verification failure during re-execution**: Any node that re-executes the transaction (e.g., for RPC `starknet_simulateTransactions`) passes the wrong hash to the account's `__validate__` entry point; the account's ECDSA check fails and the transaction is incorrectly reported as reverted.

### Likelihood Explanation

The triggering configuration (`AllResources` with non-zero `l1_gas`, zero `l2_gas`, zero `l1_data_gas`) is explicitly tested as a **valid** gateway input. Any user who submits such a transaction causes every syncing node to fail on the block that includes it. No special privilege is required; a single unprivileged transaction submission is sufficient.

### Recommendation

The serialization must preserve the variant discriminant so that deserialization can reconstruct it faithfully. Two options:

1. **Encode the variant explicitly**: add a boolean or enum field to the protobuf `ResourceBounds` message that records whether the transaction was `L1Gas` or `AllResources`, and use it unconditionally during deserialization.

2. **Reject the ambiguous configuration at the gateway**: add a stateless-validation rule that rejects `AllResources` transactions where both `l2_gas` and `l1_data_gas` are zero (forcing callers to use the `L1Gas` path), eliminating the ambiguous wire representation.

Option 1 is preferred because it is backward-compatible and does not restrict valid user configurations.

### Proof of Concept

```
1. Construct an RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      ResourceBounds::default(),   // zero
         l1_data_gas: ResourceBounds::default(),   // zero
     }

2. Compute hash_A = get_invoke_transaction_v3_hash(tx, chain_id, version)
   → uses AllResources path → 3 resource felts in Poseidon

3. Sign tx with hash_A; submit to gateway → accepted (valid_l1_gas case).

4. Primary sequencer includes tx in block B with transaction_commitment
   derived from hash_A.

5. Syncing node receives block B via P2P protobuf.
   Deserializes resource_bounds:
     l1_data_gas.is_zero() && l2_gas.is_zero() → ValidResourceBounds::L1Gas

6. Compute hash_B = get_invoke_transaction_v3_hash(deserialized_tx, chain_id, version)
   → uses L1Gas path → 2 resource felts in Poseidon
   hash_B ≠ hash_A

7. Syncing node recomputes transaction_commitment using hash_B.
   transaction_commitment_B ≠ transaction_commitment in block header.
   Block B is rejected → syncing node stalls on every block containing
   such a transaction.
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L70-82)
```rust
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

### Title
`ValidResourceBounds` Variant Collapse Through Protobuf Round-Trip Produces Wrong Transaction Hash - (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserialization of `ValidResourceBounds` uses a value-based heuristic to reconstruct the variant. When an `AllResources` transaction carries zero `l2_gas` and zero `l1_data_gas`, the round-trip collapses it to `L1Gas`. Because `get_tip_resource_bounds_hash` branches on the variant and includes a different number of resource felts in the Poseidon preimage, the hash computed after deserialization is structurally different from the hash computed at submission time.

### Finding Description

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` produces a hash over a variable-length preimage:

- `ValidResourceBounds::L1Gas` → preimage is `[tip, concat(L1_GAS, …), concat(L2_GAS, 0)]` — **2 resource felts**
- `ValidResourceBounds::AllResources` → preimage is `[tip, concat(L1_GAS, …), concat(L2_GAS, …), concat(L1_DATA, …)]` — **3 resource felts** [1](#0-0) 

The protobuf serializer for `ValidResourceBounds::L1Gas` emits an explicit zero `l1_data_gas` field: [2](#0-1) 

The protobuf deserializer then reconstructs the variant using a value-based heuristic: if both `l2_gas` and `l1_data_gas` are zero, it produces `L1Gas`; otherwise `AllResources`: [3](#0-2) 

This heuristic is **not injective**: an `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` transaction serializes to the same protobuf bytes as `L1Gas(l1_gas=X)`, and both deserialize to `L1Gas`. The original variant is permanently lost.

A user can legitimately submit an invoke transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` — the gateway's stateless validator explicitly accepts this: [4](#0-3) 

The `RpcInvokeTransactionV3` type always carries `AllResourceBounds` (never `L1Gas`): [5](#0-4) 

So the gateway computes the hash using `AllResources` (3-felt preimage). When the block is later synced via P2P and the `InvokeTransactionV3` (starknet_api type, which carries `ValidResourceBounds`) is reconstructed from protobuf, the variant collapses to `L1Gas` (2-felt preimage), producing a structurally different hash.

`validate_transaction_hash` recomputes the hash from the deserialized `Transaction` object and checks it against the stored value: [6](#0-5) 

For any transaction with `AllResources(l2=0, l1_data=0)`, this check will fail on a syncing node because the recomputed hash (from the collapsed `L1Gas` variant) does not match the stored hash (computed from `AllResources`).

### Impact Explanation

A syncing node that receives a block containing a valid transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` will compute a wrong transaction hash after protobuf deserialization. This causes hash validation to fail, leading to the syncing node either rejecting a valid block or storing an incorrect hash — a wrong state/receipt value from accepted input.

**Impact class:** Critical — Wrong state/receipt value from blockifier/execution logic for accepted input; also High — RPC execution, fee estimation, or pending view returns an authoritative-looking wrong value.

### Likelihood Explanation

Any user can trigger this by submitting an invoke V3 transaction with only L1 gas bounds set (l2_gas and l1_data_gas both zero). The gateway explicitly accepts this configuration. The collapse is deterministic and reproducible on every P2P sync of such a block.

### Recommendation

The `ValidResourceBounds` variant must be encoded explicitly in the protobuf wire format rather than inferred from field values. Add a discriminant field (e.g., `bounds_type: enum { L1_GAS_ONLY = 0, ALL_RESOURCES = 1 }`) to `protobuf::ResourceBounds`. The deserializer should use this discriminant, not a zero-value heuristic, to reconstruct the variant. Alternatively, always serialize and deserialize as `AllResources` in the P2P path (since all current RPC transactions use `AllResourceBounds`), and remove the `L1Gas` branch from the protobuf converter entirely.

### Proof of Concept

```
1. Submit invoke V3 tx with AllResourceBounds { l1_gas: {max_amount: 1000, max_price: 1}, l2_gas: {0,0}, l1_data_gas: {0,0} }
2. Gateway computes hash H_all = poseidon(INVOKE, v3, sender, poseidon(tip, concat(L1_GAS,…), concat(L2_GAS,0), concat(L1_DATA,0)), …)
3. Tx is included in block B with stored hash H_all.
4. Syncing node receives B via P2P; deserializes InvokeTransactionV3 from protobuf.
5. TryFrom<protobuf::ResourceBounds> for ValidResourceBounds: l1_data_gas.is_zero() && l2_gas.is_zero() → produces L1Gas.
6. validate_transaction_hash recomputes H_l1 = poseidon(INVOKE, v3, sender, poseidon(tip, concat(L1_GAS,…), concat(L2_GAS,0)), …)
7. H_l1 ≠ H_all → validation fails; syncing node rejects valid block or stores wrong hash.
```

### Citations

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

**File:** crates/starknet_api/src/transaction_hash.rs (L197-210)
```rust
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

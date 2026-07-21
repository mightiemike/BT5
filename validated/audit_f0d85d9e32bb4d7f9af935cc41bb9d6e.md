### Title
`ValidResourceBounds` Protobuf Round-Trip Silently Downgrades `AllResources` to `L1Gas`, Producing a Different Transaction Hash - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary
The protobuf deserialization of `ValidResourceBounds` silently converts an `AllResources` variant (with zero `l2_gas` and `l1_data_gas`) into an `L1Gas` variant. Because `get_tip_resource_bounds_hash` includes the `l1_data_gas` field in the hash only for `AllResources`, the same transaction body produces two distinct hash values depending on which representation is used. A V3 transaction submitted through the gateway with `AllResources({l1_gas, l2_gas=0, l1_data_gas=0})` receives hash H1; after a P2P protobuf round-trip the same body deserializes as `L1Gas(l1_gas)` and produces hash H2 ≠ H1.

### Finding Description

**Serialization** (`From<ValidResourceBounds> for protobuf::ResourceBounds`, `crates/apollo_protobuf/src/converters/transaction.rs` lines 471–489):

Both `L1Gas(l1_gas)` and `AllResources({l1_gas, l2_gas=0, l1_data_gas=0})` serialize to the identical protobuf wire bytes `{l1_gas: Some(l1_gas), l2_gas: Some(zero), l1_data_gas: Some(zero)}`. [1](#0-0) 

**Deserialization** (`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, lines 417–436):

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← AllResources is lost here
} else {
    ValidResourceBounds::AllResources(...)
})
``` [2](#0-1) 

**Hash divergence** (`get_tip_resource_bounds_hash`, `crates/starknet_api/src/transaction_hash.rs` lines 188–211):

The function appends the `l1_data_gas` felt to the poseidon input **only** for `AllResources`:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 felts total
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 felts total
    }
});
``` [3](#0-2) 

Even when `l1_data_gas` is zero, `get_concat_resource(&zero_bounds, L1_DATA_GAS)` produces a non-zero felt (the 7-byte resource name `"L1_DATA"` packed into the upper bits), so the two poseidon inputs differ in length and content, yielding H1 ≠ H2. [4](#0-3) 

**Concrete path:**

1. User submits `RpcInvokeTransactionV3` with `AllResourceBounds{l1_gas=X, l2_gas=0, l1_data_gas=0}`.
2. Gateway converts it to `InternalRpcInvokeTransactionV3` (always `AllResourceBounds`) and computes hash H1 via `get_invoke_transaction_v3_hash` → `get_tip_resource_bounds_hash(AllResources(...))`. [5](#0-4) 

3. The transaction is stored with `tx_hash = H1` and propagated over P2P as a `Transaction::Invoke(InvokeTransaction::V3(...))` whose `resource_bounds` field is `ValidResourceBounds::AllResources(...)`.
4. The receiving node deserializes the protobuf and obtains `ValidResourceBounds::L1Gas(l1_gas)`.
5. Any subsequent hash recomputation (e.g., in `starknet_simulateTransactions`, re-execution, or a syncing node's hash-verification pass) calls `get_tip_resource_bounds_hash(L1Gas(...))` and produces H2 ≠ H1. [6](#0-5) 

### Impact Explanation
The broken round-trip breaks the canonicalization invariant: the same signed transaction body maps to two different hashes depending on the deserialization path. A syncing node that recomputes the hash after P2P deserialization will derive H2 and either reject the transaction as having an invalid hash, or store it under H2, making it unreachable by the original hash H1. This falls under **High – Transaction conversion or signature/hash logic binds the wrong hash or executable payload**.

### Likelihood Explanation
Any V3 transaction with `l2_gas=0` and `l1_data_gas=0` (a valid configuration accepted by the stateless validator, as confirmed by the `valid_l1_gas` test case) triggers the bug on every P2P sync round-trip. No special privileges are required; a normal user submitting a standard V3 invoke with only L1 gas bounds set is sufficient. [7](#0-6) 

### Recommendation
In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, do not use the zero-bounds heuristic to infer the variant. Instead, preserve the variant explicitly — for example by adding a boolean discriminant field to the protobuf message, or by always deserializing to `AllResources` when all three resource fields are present (regardless of their values). The serializer already emits all three fields for both variants, so the fix only requires removing the downgrade condition on line 431. [8](#0-7) 

### Proof of Concept

```rust
use starknet_api::transaction::fields::{
    AllResourceBounds, ResourceBounds, ValidResourceBounds,
};
use starknet_api::transaction_hash::get_tip_resource_bounds_hash;
use starknet_api::transaction::fields::Tip;

// Construct AllResources with zero l2_gas and l1_data_gas
let all_resources = ValidResourceBounds::AllResources(AllResourceBounds {
    l1_gas: ResourceBounds { max_amount: 100.into(), max_price_per_unit: 1.into() },
    l2_gas: ResourceBounds::default(),   // zero
    l1_data_gas: ResourceBounds::default(), // zero
});

// Simulate protobuf round-trip: serialise → deserialise → L1Gas
let proto: protobuf::ResourceBounds = all_resources.into();
let after_roundtrip: ValidResourceBounds = proto.try_into().unwrap();
// after_roundtrip == ValidResourceBounds::L1Gas(...)

let tip = Tip(0);
let h1 = get_tip_resource_bounds_hash(&all_resources, &tip).unwrap();
let h2 = get_tip_resource_bounds_hash(&after_roundtrip, &tip).unwrap();

assert_ne!(h1, h2); // FAILS: same transaction, two different hashes
```

The divergence arises because `h1` is `poseidon(0, l1_gas_felt, 0, L1_DATA_packed_zero)` (3 resource felts) while `h2` is `poseidon(0, l1_gas_felt, 0)` (2 resource felts).

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

**File:** crates/starknet_api/src/transaction_hash.rs (L216-226)
```rust
fn get_concat_resource(
    resource_bounds: &ResourceBounds,
    resource_name: &ResourceName,
) -> Result<Felt, StarknetApiError> {
    let max_amount = resource_bounds.max_amount.0.to_be_bytes();
    let max_price = resource_bounds.max_price_per_unit.0.to_be_bytes();
    let concat_bytes =
        [[0_u8].as_slice(), resource_name.as_slice(), max_amount.as_slice(), max_price.as_slice()]
            .concat();
    Ok(Felt::from_bytes_be(&concat_bytes.try_into().expect("Expect 32 bytes")))
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-677)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
    fn tip(&self) -> &Tip {
        &self.tip
    }
    fn paymaster_data(&self) -> &PaymasterData {
        &self.paymaster_data
    }
    fn nonce_data_availability_mode(&self) -> &DataAvailabilityMode {
        &self.nonce_data_availability_mode
    }
    fn fee_data_availability_mode(&self) -> &DataAvailabilityMode {
        &self.fee_data_availability_mode
    }
    fn account_deployment_data(&self) -> &AccountDeploymentData {
        &self.account_deployment_data
    }
    fn sender_address(&self) -> &ContractAddress {
        &self.sender_address
    }
    fn nonce(&self) -> &Nonce {
        &self.nonce
    }
    fn calldata(&self) -> &Calldata {
        &self.calldata
    }
    fn proof_facts(&self) -> &ProofFacts {
        &self.proof_facts
    }
}

impl TransactionHasher for InternalRpcInvokeTransactionV3 {
    fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
        transaction_version: &TransactionVersion,
    ) -> Result<TransactionHash, StarknetApiError> {
        get_invoke_transaction_v3_hash(self, chain_id, transaction_version)
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

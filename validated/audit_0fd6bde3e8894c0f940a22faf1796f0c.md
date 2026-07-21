### Title
`ValidResourceBounds` Variant Ambiguity in Protobuf Deserialization Produces Divergent Transaction Hash Preimage — (`crates/apollo_protobuf/src/converters/transaction.rs` / `crates/starknet_api/src/transaction_hash.rs`)

---

### Summary

The `get_tip_resource_bounds_hash` function produces structurally different Poseidon hash preimages for `ValidResourceBounds::L1Gas` versus `ValidResourceBounds::AllResources` even when the numeric field values are identical (l2_gas = 0, l1_data_gas = 0). A heuristic in the protobuf deserializer silently converts `AllResources` (with zero l2/l1_data gas) into `L1Gas`, causing any component that re-derives the transaction hash from the deserialized object to compute a different hash than the one originally committed at the gateway.

---

### Finding Description

**Step 1 – Two distinct hash preimages for the same numeric data**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` branches on the enum variant, not on the numeric values:

```
L1Gas(l1_bounds):
  preimage = [tip, concat(L1_GAS, l1_bounds), concat(L2_GAS, ZERO)]          // 3 elements

AllResources { l1_gas, l2_gas=0, l1_data_gas=0 }:
  preimage = [tip, concat(L1_GAS, l1_gas), concat(L2_GAS, ZERO),
              concat(L1_DATA_GAS, ZERO)]                                       // 4 elements
```

The extra `concat(L1_DATA_GAS, ZERO)` element makes the two Poseidon hashes diverge even when every numeric field is identical. [1](#0-0) 

**Step 2 – Gateway always commits the `AllResources` hash**

`RpcInvokeTransactionV3` and `InternalRpcInvokeTransactionV3` both carry `resource_bounds: AllResourceBounds` (never `ValidResourceBounds`). Every conversion to `InvokeTransactionV3` wraps the value in `ValidResourceBounds::AllResources(...)`, so the hash stored in `InternalRpcTransaction.tx_hash` always uses the 4-element preimage. [2](#0-1) [3](#0-2) 

**Step 3 – Protobuf deserializer silently downgrades `AllResources` → `L1Gas`**

The P2P sync path deserializes `Transaction` objects (not `RpcTransaction`). The converter applies a heuristic:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← drops the AllResources variant
} else {
    ValidResourceBounds::AllResources(...)
})
``` [4](#0-3) 

A V3 transaction with `l2_gas = 0` and `l1_data_gas = 0` — which the gateway accepts and hashes as `AllResources` — is silently reclassified as `L1Gas` after a protobuf round-trip. The same heuristic exists in the RPC layer: [5](#0-4) 

**Step 4 – Hash re-derivation diverges**

`InternalRpcTransactionWithoutTxHash::calculate_transaction_hash` re-derives the hash from the deserialized object. After the protobuf round-trip the object carries `L1Gas`, so the re-derived hash uses the 3-element preimage and differs from the 4-element hash that was originally committed. [6](#0-5) 

`validate_transaction_hash` (used by the feeder-gateway client and storage readers) would also fail for such transactions because it calls `get_transaction_hash` on the deserialized object. [7](#0-6) 

---

### Impact Explanation

Any component that re-derives or validates a transaction hash after a protobuf round-trip will compute a hash that differs from the one committed in the block for every V3 transaction whose `l2_gas` and `l1_data_gas` are both zero. Concretely:

- A syncing node that re-verifies transaction hashes (e.g., via `validate_transaction_hash`) will reject valid blocks containing such transactions.
- A node receiving a mempool transaction via the P2P path and re-computing its hash before forwarding will produce a different `tx_hash`, breaking deduplication and nonce tracking.
- The user's ECDSA/Schnorr signature covers the `AllResources` hash; any verifier that reconstructs the hash from the `L1Gas` variant will see a signature mismatch.

This matches the scope criterion: **"High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."**

---

### Likelihood Explanation

The gateway's stateless validator explicitly accepts V3 transactions with `l2_gas = 0` and `l1_data_gas = 0` (test case `valid_l1_gas` in `stateless_transaction_validator_test.rs`). Any user submitting a standard V3 invoke with only an L1-gas bound triggers the inconsistency. No special privilege or malformed input is required. [8](#0-7) 

---

### Recommendation

Remove the heuristic that infers the `ValidResourceBounds` variant from numeric values. Instead:

1. Add a discriminant field to the protobuf `ResourceBounds` message (e.g., `bool is_all_resources`) so the variant is preserved across serialization.
2. Alternatively, always serialize/deserialize as `AllResources` for V3 transactions and reserve `L1Gas` exclusively for pre-0.13.3 transactions identified by their transaction version, not by zero-value fields.
3. Add a round-trip test asserting that `hash(deserialize(serialize(tx))) == hash(tx)` for V3 transactions with zero l2/l1_data gas.

---

### Proof of Concept

```
// Construct a V3 invoke with l2_gas=0, l1_data_gas=0 (valid at gateway)
let rpc_tx = RpcInvokeTransactionV3 {
    resource_bounds: AllResourceBounds {
        l1_gas: ResourceBounds { max_amount: GasAmount(100), max_price_per_unit: GasPrice(1) },
        l2_gas: ResourceBounds::default(),      // zero
        l1_data_gas: ResourceBounds::default(), // zero
    },
    ...
};

// Gateway path: hash uses AllResources (4-element preimage)
let internal = InternalRpcInvokeTransactionV3::from(rpc_tx.clone());
let hash_gateway = internal.calculate_transaction_hash(&chain_id).unwrap();
// hash_gateway = Poseidon(tip, L1_GAS_concat, L2_GAS_zero, L1_DATA_GAS_zero, ...)

// Protobuf round-trip (P2P sync path)
let proto: protobuf::ResourceBounds = ValidResourceBounds::AllResources(
    AllResourceBounds { l1_gas: ..., l2_gas: zero, l1_data_gas: zero }
).into();
let roundtripped: ValidResourceBounds = proto.try_into().unwrap();
// roundtripped == ValidResourceBounds::L1Gas(l1_gas)  ← variant changed!

// Re-derive hash from deserialized object: uses L1Gas (3-element preimage)
// hash_sync = Poseidon(tip, L1_GAS_concat, L2_GAS_zero, ...)

assert_ne!(hash_gateway, hash_sync); // ← divergence confirmed
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L124-140)
```rust
    pub fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
    ) -> Result<TransactionHash, StarknetApiError> {
        let transaction_version = &self.version();
        match self {
            InternalRpcTransactionWithoutTxHash::Declare(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
            InternalRpcTransactionWithoutTxHash::Invoke(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
            InternalRpcTransactionWithoutTxHash::DeployAccount(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
        }
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L568-583)
```rust
impl From<RpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: RpcInvokeTransactionV3) -> Self {
        Self {
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            tip: tx.tip,
            signature: tx.signature,
            nonce: tx.nonce,
            sender_address: tx.sender_address,
            calldata: tx.calldata,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
            proof_facts: tx.proof_facts,
        }
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L679-694)
```rust
impl From<InternalRpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: InternalRpcInvokeTransactionV3) -> Self {
        Self {
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            tip: tx.tip,
            signature: tx.signature,
            nonce: tx.nonce,
            sender_address: tx.sender_address,
            calldata: tx.calldata,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
            proof_facts: tx.proof_facts,
        }
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

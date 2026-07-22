### Title
`ValidResourceBounds::AllResources` with zero L2/L1-data gas silently collapses to `L1Gas` after protobuf round-trip, producing a divergent transaction hash - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` applies a zero-check that silently converts `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` into `L1Gas(X)`. Because `get_tip_resource_bounds_hash` produces structurally different hash chains for the two variants, the transaction hash computed at the originating node (using the `AllResources` formula) differs from the hash computed at any node that received the same transaction over P2P protobuf (using the `L1Gas` formula). An unprivileged user can trigger this with a fully valid gateway submission, causing the transaction commitment embedded in the block hash to be unverifiable by syncing peers.

---

### Finding Description

**Serialization path** (`From<ValidResourceBounds> for protobuf::ResourceBounds`): [1](#0-0) 

When `ValidResourceBounds::AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is serialized, all three protobuf fields are emitted with their actual (zero) values.

**Deserialization path** (`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`): [2](#0-1) 

On the receiving side, the zero-check `if l1_data_gas.is_zero() && l2_gas.is_zero()` fires and the variant is reconstructed as `L1Gas(X)` — a different Rust enum arm than what was serialized.

**Hash domain boundary** (`get_tip_resource_bounds_hash`): [3](#0-2) 

- For `L1Gas(X)`: the hash chain is `Poseidon(tip, L1_GAS_concat, L2_GAS_concat(0))` — **two** resource elements.
- For `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`: the chain is `Poseidon(tip, L1_GAS_concat, L2_GAS_concat(0), L1_DATA_GAS_concat(0))` — **three** resource elements.

These produce distinct felt values, so the full transaction hash diverges.

**Originating node stores `AllResources`** because `InternalRpcInvokeTransactionV3 → InvokeTransactionV3` always wraps with `ValidResourceBounds::AllResources`: [4](#0-3) 

**Gateway accepts the triggering transaction** — the stateless validator explicitly allows `AllResourceBounds` with only `l1_gas` non-zero: [5](#0-4) 

**Transaction hash is computed from `InternalRpcTransactionWithoutTxHash`** using the `AllResources` path: [6](#0-5) 

---

### Impact Explanation

A user submits a V3 invoke with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`. The gateway accepts it, computes hash **H₁** (three-element Poseidon chain), and the sequencer includes it in a block. The block's `transaction_commitment` is built from **H₁**. [7](#0-6) 

Any peer that receives this block via P2P sync deserializes the transaction's resource bounds as `L1Gas` and recomputes hash **H₂** (two-element chain). **H₁ ≠ H₂**, so the peer's recomputed `transaction_commitment` diverges from the one committed in the block hash, causing block hash verification failure and preventing the peer from accepting the block. This matches the **High** impact scope: *"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."*

---

### Likelihood Explanation

The trigger requires only a standard V3 invoke transaction submitted through the public gateway with `l2_gas = 0` and `l1_data_gas = 0` — both of which are zero by default in `AllResourceBounds::default()`. No privileged access, no malformed bytes, and no special network position is required. The gateway's stateless validator explicitly permits this combination. Any user who knows the protobuf deserialization rule can craft such a transaction deterministically.

---

### Recommendation

In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, remove the zero-check that collapses `AllResources` to `L1Gas`. Instead, preserve the variant exactly as serialized. The `L1Gas` variant should only be produced when the serializer explicitly encoded it (i.e., when the originating `ValidResourceBounds` was already `L1Gas`). One approach is to add a discriminant field to the protobuf message, or to treat the presence of a non-`None` `l1_data_gas` field as the canonical signal for `AllResources` regardless of its value.

Alternatively, enforce at the gateway that any `AllResourceBounds` with zero `l2_gas` and zero `l1_data_gas` is normalized to `L1Gas` before hash computation, so the two representations are never mixed within the same transaction lifecycle.

---

### Proof of Concept

```rust
use starknet_api::transaction::fields::{
    AllResourceBounds, ResourceBounds, ValidResourceBounds, GasAmount, GasPrice,
};
use starknet_api::transaction_hash::get_tip_resource_bounds_hash;
use starknet_api::transaction::fields::Tip;

// Craft the triggering resource bounds: AllResources with zero l2/l1_data
let all_resources = ValidResourceBounds::AllResources(AllResourceBounds {
    l1_gas: ResourceBounds { max_amount: GasAmount(1000), max_price_per_unit: GasPrice(1) },
    l2_gas: ResourceBounds::default(),      // zero
    l1_data_gas: ResourceBounds::default(), // zero
});

// Simulate protobuf round-trip collapse
let l1_gas_only = ValidResourceBounds::L1Gas(
    ResourceBounds { max_amount: GasAmount(1000), max_price_per_unit: GasPrice(1) }
);

let tip = Tip(0);
let hash_all = get_tip_resource_bounds_hash(&all_resources, &tip).unwrap();
let hash_l1  = get_tip_resource_bounds_hash(&l1_gas_only,  &tip).unwrap();

// These MUST be equal for hash consistency, but they are NOT:
assert_ne!(hash_all, hash_l1,
    "Hash diverges: AllResources(zero l2/data) != L1Gas after protobuf round-trip");
// → assertion passes, confirming the divergence
```

The divergence propagates into the full `get_invoke_transaction_v3_hash` output, causing the transaction commitment in the block hash to be unverifiable by any node that received the transaction via P2P protobuf deserialization. [8](#0-7)

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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L245-282)
```rust
pub fn calculate_block_hash(
    partial_block_hash_components: &PartialBlockHashComponents,
    state_root: GlobalRoot,
    previous_block_hash: BlockHash,
) -> StarknetApiResult<BlockHash> {
    let block_hash_version: BlockHashVersion =
        partial_block_hash_components.starknet_version.try_into()?;
    let block_commitments = &partial_block_hash_components.header_commitments;
    Ok(BlockHash(
        HashChain::new()
            .chain(&block_hash_version.clone().into())
            .chain(&partial_block_hash_components.block_number.0.into())
            .chain(&state_root.0)
            .chain(&partial_block_hash_components.sequencer.0)
            .chain(&partial_block_hash_components.timestamp.0.into())
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
            .chain(&block_commitments.event_commitment.0)
            .chain(&block_commitments.receipt_commitment.0)
            .chain_iter(
                gas_prices_to_hash(
                    &partial_block_hash_components.l1_gas_price,
                    &partial_block_hash_components.l1_data_gas_price,
                    &partial_block_hash_components.l2_gas_price,
                    &block_hash_version,
                )
                .iter(),
            )
            .chain(
                &Felt::try_from(&partial_block_hash_components.starknet_version)
                    .expect("Expect ASCII version"),
            )
            .chain(&Felt::ZERO)
            .chain(&previous_block_hash.0)
            .get_poseidon_hash(),
    ))
}
```

### Title
`get_tip_resource_bounds_hash` produces divergent hashes for `AllResources{l2_gas=0, l1_data_gas=0}` vs `L1Gas` — protobuf conversion silently changes variant, breaking hash canonicalization — (File: `crates/starknet_api/src/transaction_hash.rs`, `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

`get_tip_resource_bounds_hash` uses a **variable-length Poseidon preimage** gated on the `ValidResourceBounds` variant: `L1Gas` hashes 3 elements while `AllResources` hashes 4 elements. The protobuf converter silently downgrades `AllResources` to `L1Gas` whenever `l2_gas == 0 && l1_data_gas == 0`. A transaction submitted via the RPC gateway with `AllResources{l2_gas=0, l1_data_gas=0}` receives hash H4 (4-element preimage); when the same transaction is deserialized from protobuf during P2P sync, it becomes `L1Gas` and the hash recomputes to H3 (3-element preimage). Because `Poseidon::hash_array` is length-aware, H3 ≠ H4, breaking the canonicalization invariant that a transaction's hash is independent of its serialization path.

### Finding Description

**Root cause — variable-length preimage in `get_tip_resource_bounds_hash`:**

`crates/starknet_api/src/transaction_hash.rs` lines 188–211 conditionally appends the `l1_data_gas` packed felt only for `AllResources`:

```rust
// L1 and L2 gas bounds always exist.
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];

// For new V3 txs, need to also hash the data gas bounds.
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 3-element preimage
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 4-element preimage
    }
});

Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
```

When `l2_gas = 0` and `l1_data_gas = 0`:

| Variant | Preimage elements | Hash |
|---|---|---|
| `L1Gas` | `[tip, l1_gas_packed, 0]` | H3 |
| `AllResources` | `[tip, l1_gas_packed, 0, 0]` | H4 |

`HashChain::get_poseidon_hash()` calls `Poseidon::hash_array(self.elements.as_slice())`, which is length-aware (it appends `[1, 0]` padding after the elements before the final permutation). Therefore H3 ≠ H4 even though the actual resource values are identical.

**Trigger — protobuf converter silently changes the variant:**

`crates/apollo_protobuf/src/converters/transaction.rs` lines 431–435:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← silently downgrades AllResources
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

The same downgrade logic exists in the RPC deserialization path at `crates/apollo_rpc/src/v0_8/transaction.rs` lines 190–191:

```rust
if value.l1_data_gas.is_zero() && value.l2_gas.is_zero() {
    Self::L1Gas(value.l1_gas)
```

**Divergence path:**

1. User submits `RpcInvokeTransactionV3` (which always carries `AllResourceBounds`) with `l2_gas = 0`, `l1_data_gas = 0`, non-zero `l1_gas`.
2. Gateway converts to `InternalRpcInvokeTransactionV3` preserving `AllResourceBounds`, computes hash H4 via `get_invoke_transaction_v3_hash` → `get_tip_resource_bounds_hash` with `AllResources` (4-element preimage).
3. Transaction is included in a block; H4 is stored as the canonical hash.
4. Block is propagated over P2P. The `InvokeTransactionV3` is serialized to protobuf `ResourceBounds` with `l2_gas = 0`, `l1_data_gas = 0`.
5. Receiving node deserializes via `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` → `L1Gas` (because `l1_data_gas.is_zero() && l2_gas.is_zero()`).
6. Hash is recomputed as H3 (3-element preimage). H3 ≠ H4.
7. `validate_transaction_hash` rejects the transaction; the block fails verification on the receiving node.

### Impact Explanation

The transaction hash is the canonical identity of a Starknet transaction. It is committed to in the block hash, used for signature verification, and used as the deduplication key in the mempool. When the hash computed at submission time (H4, `AllResources` path) diverges from the hash recomputed after P2P deserialization (H3, `L1Gas` path), any node that re-derives the hash from the deserialized transaction will reject the block as invalid. This causes a network split: the sequencer that produced the block accepts it, but syncing nodes reject it. The core invariant — that a transaction's hash is canonical and independent of serialization path — is broken.

### Likelihood Explanation

The trigger condition (`AllResources` with `l2_gas = 0` and `l1_data_gas = 0`) is unusual but reachable. The gateway's `validate_resource_bounds` only checks the l2_gas price against the previous block's price; it does not require `l2_gas.max_amount > 0` for `AllResources` transactions. A user can deliberately or accidentally submit such a transaction. The protobuf downgrade fires unconditionally on any such transaction that passes through P2P sync.

### Recommendation

The protobuf converter should preserve the `AllResources` variant even when `l2_gas` and `l1_data_gas` are zero. The downgrade to `L1Gas` must only occur for transactions that were originally submitted as `L1Gas` (pre-0.13.3). One approach is to transmit a version discriminator in the protobuf message to distinguish `L1Gas` from `AllResources{l2=0, l1_data=0}`. Alternatively, `get_tip_resource_bounds_hash` should always include the `l1_data_gas` element for all V3 transactions regardless of variant, eliminating the length ambiguity entirely.

### Proof of Concept

```
// Step 1: Submit via RPC gateway
RpcInvokeTransactionV3 {
    resource_bounds: AllResourceBounds {
        l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
        l2_gas:      ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },  // zero
        l1_data_gas: ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },  // zero
    },
    ...
}

// Step 2: Gateway computes hash H4 via AllResources path
// get_tip_resource_bounds_hash preimage = [tip, l1_gas_packed, l2_gas_packed=0, l1_data_gas_packed=0]
// H4 = Poseidon::hash_array([tip, l1_gas_packed, 0, 0])

// Step 3: Block is produced with H4 as the transaction hash

// Step 4: P2P sync — protobuf deserialization
// TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
//   l1_data_gas.is_zero() && l2_gas.is_zero() → ValidResourceBounds::L1Gas(l1_gas)

// Step 5: Receiving node recomputes hash H3 via L1Gas path
// get_tip_resource_bounds_hash preimage = [tip, l1_gas_packed, l2_gas_packed=0]
// H3 = Poseidon::hash_array([tip, l1_gas_packed, 0])

// Step 6: H3 ≠ H4 → validate_transaction_hash fails → block rejected
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** crates/starknet_api/src/crypto/utils.rs (L119-122)
```rust
    // Returns the poseidon hash of the chained felts.
    pub fn get_poseidon_hash(&self) -> StarkHash {
        Poseidon::hash_array(self.elements.as_slice())
    }
```

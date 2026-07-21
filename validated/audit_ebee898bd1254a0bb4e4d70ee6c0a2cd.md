### Title
`ValidResourceBounds::AllResources` with zero L2/L1-data-gas silently reclassified to `L1Gas` during protobuf deserialization, producing a divergent transaction hash — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` applies a zero-value check that silently reclassifies `AllResources{l2_gas=0, l1_data_gas=0}` to `L1Gas`. Because `get_tip_resource_bounds_hash` includes `l1_data_gas` in the hash preimage only for `AllResources`, the two variants produce **different hashes** for numerically identical bounds. A V3 transaction originally hashed and signed under `AllResources` is re-hashed under `L1Gas` after P2P sync, binding the wrong hash to the transaction.

---

### Finding Description

**Root cause — protobuf deserializer reclassifies on zero:**

In `crates/apollo_protobuf/src/converters/transaction.rs` lines 431–435, the `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` implementation applies:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

Any `AllResources` transaction where both `l2_gas` and `l1_data_gas` are zero is silently reclassified to `L1Gas` upon deserialization. The same reclassification exists in the RPC layer at `crates/apollo_rpc/src/v0_8/transaction.rs` lines 188–199.

**Hash domain divergence — `get_tip_resource_bounds_hash` is variant-sensitive:**

In `crates/starknet_api/src/transaction_hash.rs` lines 202–208, `l1_data_gas` is included in the hash preimage **only** for `AllResources`:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // l1_data_gas NOT hashed
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // l1_data_gas hashed
    }
});
```

Therefore `AllResources{l1_gas=X, l2_gas=0, l1_data_gas=0}` and `L1Gas{l1_gas=X}` produce **different Poseidon hashes** even though their numeric values are identical. The `AllResources` hash includes a zero `l1_data_gas` felt in the preimage; the `L1Gas` hash does not.

**The `ValidResourceBounds` enum is defined in `crates/starknet_api/src/transaction/fields.rs` lines 363–366:**

```rust
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds),       // Pre 0.13.3. Only L1 gas.
    AllResources(AllResourceBounds),
}
```

The variant is a semantic type tag, not just a numeric representation. Reclassifying it based on zero values destroys this semantic distinction.

**Concrete attack path:**

1. A user submits a V3 invoke transaction with `AllResourceBounds{l1_gas=X, l2_gas=0, l1_data_gas=0}` via the gateway. The gateway converts it to `InvokeTransactionV3` with `ValidResourceBounds::AllResources(...)` (via `From<RpcInvokeTransactionV3> for InvokeTransactionV3` at `crates/starknet_api/src/rpc_transaction.rs` line 571: `resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds)`). The hash `tx_hash_A` is computed including the zero `l1_data_gas` felt. The user signs over `tx_hash_A`.

2. The transaction is included in a block and propagated via P2P sync. The `InvokeTransactionV3` is serialized to `protobuf::ResourceBounds` (which carries all three fields as `Option<ResourceLimits>`).

3. A syncing node deserializes the protobuf. Because `l1_data_gas.is_zero() && l2_gas.is_zero()`, the deserializer at `crates/apollo_protobuf/src/converters/transaction.rs` line 431 reclassifies to `ValidResourceBounds::L1Gas`. The node recomputes `tx_hash_B` — which does **not** include `l1_data_gas` in the preimage. `tx_hash_A ≠ tx_hash_B`.

4. The syncing node now holds a transaction whose recomputed hash does not match the user's signature, or rejects the block as having an invalid transaction hash — causing a chain split between nodes that processed the transaction natively and nodes that received it via sync.

**This is the direct analog to the BendDAO bug:** just as `scaledAmount` reaching zero caused `loanStatus` and `LockerAddr` to not be updated (state inconsistency when a value hits zero), here `l2_gas` and `l1_data_gas` reaching zero causes `ValidResourceBounds` to be reclassified (hash domain inconsistency when values hit zero), breaking the invariant that the hash uniquely identifies the transaction across the serialization boundary.

---

### Impact Explanation

**High — Transaction conversion or signature/hash logic binds the wrong hash.**

A V3 transaction with zero L2/L1-data-gas bounds (a valid and common pattern — the test data in `crates/apollo_starknet_client/resources/reader/block_post_0_14_2.json` shows many such transactions) is assigned a different hash after P2P sync deserialization than the hash it was signed with. This causes:

- Syncing nodes to reject valid blocks (chain split / liveness failure), or
- Syncing nodes to store transactions with wrong hashes, producing wrong receipts and wrong RPC responses for `starknet_getTransactionByHash`.

---

### Likelihood Explanation

**Medium.** The trigger condition — a V3 transaction with `l2_gas = {0, 0}` and `l1_data_gas = {0, 0}` — is valid and observable in production data. Any node that syncs such a transaction from a peer (rather than processing it natively through the gateway) will hit the reclassification. The condition is not adversarially exotic; it arises naturally for transactions that only consume L1 gas.

---

### Recommendation

Remove the zero-value reclassification from the protobuf deserializer. The `ValidResourceBounds` variant must be preserved as transmitted, not inferred from zero values. The serializer already encodes all three resource bounds fields unconditionally for both variants (lines 471–489 of `crates/apollo_protobuf/src/converters/transaction.rs`), so a discriminant field or a separate protobuf enum tag should be added to round-trip the variant faithfully.

Concretely, in `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, replace the zero-check with an explicit variant field, or always deserialize as `AllResources` when all three fields are present (since the P2P sync path only carries V3 transactions which always use `AllResources`):

```rust
// Always AllResources when all three fields are present (V3 transactions).
Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))
```

The same fix applies to `From<ResourceBoundsMapping> for ValidResourceBounds` in `crates/apollo_rpc/src/v0_8/transaction.rs`.

---

### Proof of Concept

```rust
use starknet_api::transaction::fields::{
    AllResourceBounds, GasAmount, GasPrice, ResourceBounds, ValidResourceBounds,
};
use starknet_api::transaction_hash::get_tip_resource_bounds_hash;
use starknet_api::transaction::fields::Tip;
use apollo_protobuf::protobuf;

// Step 1: Construct AllResources with zero l2/l1_data (valid V3 tx pattern).
let all_resources = ValidResourceBounds::AllResources(AllResourceBounds {
    l1_gas: ResourceBounds { max_amount: GasAmount(100), max_price_per_unit: GasPrice(1) },
    l2_gas: ResourceBounds::default(),       // zero
    l1_data_gas: ResourceBounds::default(),  // zero
});

// Step 2: Serialize to protobuf (as done during P2P sync).
let proto: protobuf::ResourceBounds = all_resources.into();

// Step 3: Deserialize — reclassified to L1Gas due to zero check.
let deserialized: ValidResourceBounds = proto.try_into().unwrap();
// deserialized == ValidResourceBounds::L1Gas(...)  ← WRONG variant

// Step 4: Compute hashes — they differ.
let hash_all = get_tip_resource_bounds_hash(&all_resources, &Tip(0)).unwrap();
let hash_l1  = get_tip_resource_bounds_hash(&deserialized,  &Tip(0)).unwrap();

assert_ne!(hash_all, hash_l1);
// hash_all includes poseidon([tip, l1_gas_packed, l2_gas_packed, l1_data_gas_packed])
// hash_l1  includes poseidon([tip, l1_gas_packed, l2_gas_packed])
// The user signed over hash_all; the syncing node recomputes hash_l1.
// Signature verification fails on the syncing node.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

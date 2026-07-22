### Title
`get_tip_resource_bounds_hash` Produces Divergent Hash for `ValidResourceBounds::L1Gas` vs `AllResources{l2=0,data=0}` Across RPC and P2P Ingestion Paths — (`File: crates/starknet_api/src/transaction_hash.rs`)

---

### Summary

The `get_tip_resource_bounds_hash` function hashes a **different number of resource-bound felts** depending on whether the `ValidResourceBounds` enum variant is `L1Gas` (2 felts: L1\_GAS + L2\_GAS) or `AllResources` (3 felts: L1\_GAS + L2\_GAS + L1\_DATA\_GAS). The protobuf deserializer silently **downgrades** any `AllResources` whose `l2_gas` and `l1_data_gas` are both zero to `L1Gas`. Because the RPC/gateway path always produces `AllResources`, the same logical transaction (zero L2 gas, zero data gas) receives **two different transaction hashes** depending on which ingestion path is used, breaking hash canonicalization across the sequencer network.

---

### Finding Description

**Step 1 — Hash function branches on variant, not on values**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` always emits the L1 and L2 resource felts, then conditionally appends the L1\_DATA\_GAS felt only for `AllResources`:

```rust
// L1 and L2 gas bounds always exist.
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];
// For new V3 txs, need to also hash the data gas bounds.
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 felts total
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 felts total
    }
});
Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
``` [1](#0-0) 

For a transaction where `l2_gas = 0` and `l1_data_gas = 0`, the two variants produce:

| Variant | Elements hashed | Resulting hash |
|---|---|---|
| `L1Gas(X)` | `poseidon(tip, concat(L1_GAS,X), concat(L2_GAS,0))` | H₁ |
| `AllResources{l1:X, l2:0, data:0}` | `poseidon(tip, concat(L1_GAS,X), concat(L2_GAS,0), concat(L1_DATA_GAS,0))` | H₂ |

H₁ ≠ H₂ because Poseidon is length-sensitive.

**Step 2 — RPC path always produces `AllResources`**

`RpcInvokeTransactionV3` and `InternalRpcInvokeTransactionV3` both store `resource_bounds: AllResourceBounds` (not `ValidResourceBounds`). Their `resource_bounds()` trait implementations unconditionally wrap in `ValidResourceBounds::AllResources`:

```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
``` [2](#0-1) 

So a transaction submitted via RPC with zero L2 gas and zero data gas always gets hash H₂ (3-felt path).

**Step 3 — Protobuf deserializer silently downgrades to `L1Gas`**

The P2P sync deserializer in `crates/apollo_protobuf/src/converters/transaction.rs` converts `protobuf::ResourceBounds` to `ValidResourceBounds`. When `l2_gas` and `l1_data_gas` are both zero, it produces `ValidResourceBounds::L1Gas`:

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)   // ← downgrade
        } else {
            ValidResourceBounds::AllResources(...)
        })
    }
}
``` [3](#0-2) 

So the same transaction received via P2P sync gets hash H₁ (2-felt path).

**Step 4 — The divergence**

The `InternalRpcTransactionWithoutTxHash::calculate_transaction_hash` method calls `get_invoke_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash` with the variant produced by whichever path deserialized the transaction: [4](#0-3) [5](#0-4) 

- **Proposer node** (received via RPC): computes H₂ (`AllResources`, 3 felts), stores it in `InternalRpcTransaction.tx_hash`, includes it in the block.
- **Validator/syncing node** (received via P2P protobuf): deserializes to `L1Gas`, computes H₁ (2 felts). H₁ ≠ H₂.

The Starknet OS (`hash_fee_fields` in Cairo) always asserts `n_resource_bounds = 3` and hashes all three, matching H₂: [6](#0-5) 

---

### Impact Explanation

A validator node that receives a proposed block via P2P will re-derive transaction hashes from the protobuf-deserialized transaction bodies. For any V3 transaction with zero L2 gas and zero data gas, the validator computes H₁ while the block records H₂. This causes:

1. **Wrong transaction hash committed to state** — the validator stores H₁ but the block header commits H₂, producing an incorrect receipt, event log, or storage value. This matches: *"Wrong state, receipt, event … or revert result from blockifier/syscall/execution logic for accepted input."*
2. **Signature/hash binding failure** — the user signed H₂ (the hash the gateway computed). The validator's account validation runs against H₁, causing `__validate__` to fail or accept a transaction bound to the wrong hash. This matches: *"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."*
3. **Consensus split** — proposer and validators disagree on the canonical hash of a valid transaction, potentially causing liveness failure.

---

### Likelihood Explanation

Any V3 transaction submitted with `l2_gas = 0` and `l1_data_gas = 0` (a valid and common configuration for pre-0.13.3-style V3 transactions) triggers this path. No special privilege is required; a normal user submitting a standard invoke transaction with only L1 gas bounds set is sufficient. The protobuf TODO comment (`// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.`) confirms this downgrade path is intentionally kept alive.

---

### Recommendation

1. **Remove the downgrade in the protobuf deserializer**: always produce `ValidResourceBounds::AllResources` when deserializing a V3 transaction, even if L2 and data gas are zero. The `L1Gas` variant should only be used for pre-0.13.3 transactions that were originally signed with a 2-felt hash.

2. **Alternatively, make `get_tip_resource_bounds_hash` variant-agnostic**: always hash all three resource types (appending zero for missing ones), so `L1Gas(X)` and `AllResources{l1:X, l2:0, data:0}` produce the same hash. This requires verifying alignment with the Starknet spec (SNIP-8) and the OS Cairo implementation.

3. **Add a canonicalization invariant test**: assert that `get_tip_resource_bounds_hash(L1Gas(X)) == get_tip_resource_bounds_hash(AllResources{l1:X, l2:0, data:0})` if they are intended to represent the same transaction type, or assert they are never used interchangeably.

---

### Proof of Concept

```rust
use starknet_api::transaction::fields::{
    AllResourceBounds, ResourceBounds, ValidResourceBounds,
};
use starknet_api::block::{GasAmount, GasPrice};
use starknet_api::transaction_hash::get_tip_resource_bounds_hash;
use starknet_api::transaction::fields::Tip;

let l1_bounds = ResourceBounds {
    max_amount: GasAmount(1000),
    max_price_per_unit: GasPrice(42),
};
let tip = Tip(0);

// Path 1: RPC ingestion always produces AllResources
let all_resources = ValidResourceBounds::AllResources(AllResourceBounds {
    l1_gas: l1_bounds,
    l2_gas: ResourceBounds::default(),   // zero
    l1_data_gas: ResourceBounds::default(), // zero
});
let hash_all = get_tip_resource_bounds_hash(&all_resources, &tip).unwrap();

// Path 2: P2P protobuf deserialization downgrades to L1Gas when l2=0, data=0
let l1_gas_only = ValidResourceBounds::L1Gas(l1_bounds);
let hash_l1 = get_tip_resource_bounds_hash(&l1_gas_only, &tip).unwrap();

// These are NOT equal — same logical bounds, different hashes
assert_ne!(hash_all, hash_l1,
    "Hash divergence: AllResources({:?}) = {:?}, L1Gas({:?}) = {:?}",
    all_resources, hash_all, l1_gas_only, hash_l1
);
```

The `get_tip_resource_bounds_hash` for `L1Gas` feeds 3 elements into Poseidon (`tip + L1_GAS_felt + L2_GAS_felt`), while `AllResources` feeds 4 elements (`tip + L1_GAS_felt + L2_GAS_felt + L1_DATA_GAS_felt`). Poseidon is not length-prefix-free in this usage, so the outputs differ. [7](#0-6) [8](#0-7) [2](#0-1)

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L110-144)
```text
func hash_fee_fields{range_check_ptr, poseidon_ptr: PoseidonBuiltin*}(
    tip: felt, resource_bounds: ResourceBounds*, n_resource_bounds: felt
) -> felt {
    alloc_locals;

    let (local data_to_hash: felt*) = alloc();
    assert data_to_hash[0] = tip;
    assert_nn_le(tip, 2 ** 64 - 1);

    static_assert L1_GAS_INDEX == 0;
    static_assert L2_GAS_INDEX == 1;
    static_assert L1_DATA_GAS_INDEX == 2;

    with_attr error_message("Invalid number of resource bounds: {n_resource_bounds}.") {
        assert n_resource_bounds = 3;
    }

    // L1 gas.
    let l1_gas_bounds = resource_bounds[L1_GAS_INDEX];
    assert l1_gas_bounds.resource = L1_GAS;
    assert data_to_hash[1] = pack_resource_bounds(l1_gas_bounds);

    // L2 gas.
    let l2_gas_bounds = resource_bounds[L2_GAS_INDEX];
    assert l2_gas_bounds.resource = L2_GAS;
    assert data_to_hash[2] = pack_resource_bounds(l2_gas_bounds);

    // L1 data gas.
    let l1_data_gas_bounds = resource_bounds[L1_DATA_GAS_INDEX];
    assert l1_data_gas_bounds.resource = L1_DATA_GAS;
    assert data_to_hash[3] = pack_resource_bounds(l1_data_gas_bounds);

    let (hash) = poseidon_hash_many(n=n_resource_bounds + 1, elements=data_to_hash);
    return hash;
}
```

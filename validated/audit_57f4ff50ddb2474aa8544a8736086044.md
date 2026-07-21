### Title
Cairo OS `hash_fee_fields` Hardcodes `n_resource_bounds = 3` While Rust `get_tip_resource_bounds_hash` Uses Actual Count, Producing Divergent Transaction Hash Preimages for `ValidResourceBounds::L1Gas` Transactions — (`crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

The Cairo OS `hash_fee_fields` function unconditionally asserts `n_resource_bounds = 3` and hashes four elements `(tip, L1Gas, L2Gas, L1DataGas)`. The Rust `get_tip_resource_bounds_hash` function branches on the `ValidResourceBounds` variant: for `L1Gas` it hashes only three elements `(tip, L1Gas, L2Gas_zero)`, omitting `L1DataGas`. For any V3 transaction carrying `ValidResourceBounds::L1Gas` bounds, the two sides produce structurally different Poseidon hashes, so the OS-recomputed transaction hash never matches the hash committed by the sequencer.

---

### Finding Description

**Rust side — `get_tip_resource_bounds_hash`** (`crates/starknet_api/src/transaction_hash.rs`, lines 188–211):

```rust
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],          // ← only 2 bounds
    ValidResourceBounds::AllResources(all_resources) =>
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?],
});
Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
```

For `L1Gas`: `poseidon_hash_many(n=3, [tip, L1Gas, L2Gas_zero])`.

**Cairo OS side — `hash_fee_fields`** (`transaction_hash.cairo`, lines 110–144):

```cairo
with_attr error_message("Invalid number of resource bounds: {n_resource_bounds}.") {
    assert n_resource_bounds = 3;   // ← always 3
}
// accesses resource_bounds[0], [1], [2]
let (hash) = poseidon_hash_many(n=n_resource_bounds + 1, elements=data_to_hash);
```

For every transaction: `poseidon_hash_many(n=4, [tip, L1Gas, L2Gas_zero, L1DataGas_zero])`.

**`get_account_tx_common_fields`** (`transaction_impls.cairo`, line 190) hardcodes `n_resource_bounds=3` unconditionally, so the hint `LoadCommonTxFields` must pad a `L1Gas` transaction with a zero `L1DataGas` entry to satisfy the Cairo memory model. The result is a 4-element hash on the OS side versus a 3-element hash on the Rust side — a canonical divergence. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

When the OS re-executes a block containing a `ValidResourceBounds::L1Gas` V3 transaction, it recomputes the transaction hash using `hash_fee_fields` (4-element Poseidon) and compares it against the hash committed by the sequencer (3-element Poseidon). The hashes differ, so the OS either:

- Rejects the transaction as having an invalid hash → wrong revert result for an accepted, valid transaction, or
- Produces a proof over an incorrect execution trace.

This matches **Critical: Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input.**

`ValidResourceBounds::L1Gas` is explicitly supported as a live variant (pre-0.13.3 V3 transactions). The gateway accepts them, the blockifier executes them, and the OS must prove them. No version gate in `compute_invoke_transaction_hash` excludes them — the only check is `assert version = 3`. [4](#0-3) 

---

### Likelihood Explanation

Any V3 transaction submitted with `ValidResourceBounds::L1Gas` bounds (a valid, accepted transaction type) triggers the divergence. An attacker or ordinary user submitting such a transaction to a node that runs OS-based proving will cause the proof to fail or the OS to produce a wrong execution result. The trigger requires no privilege — it is a normal transaction submission.

---

### Recommendation

Align the two hash computations. Either:

1. **Rust side**: When `ValidResourceBounds::L1Gas`, append a zero `L1DataGas` entry so the preimage is always `(tip, L1Gas, L2Gas_zero, L1DataGas_zero)` — matching the Cairo OS's fixed 4-element layout.
2. **Cairo OS side**: Pass and use the actual `n_resource_bounds` from the transaction (2 for `L1Gas`, 3 for `AllResources`) instead of hardcoding 3, and branch the hash accordingly — matching the Rust logic.

Option 1 is simpler and keeps the Cairo OS unchanged. A regression test should assert that `get_tip_resource_bounds_hash` with a `L1Gas` variant produces the same felt as `hash_fee_fields` called with the same data.

---

### Proof of Concept

```
Transaction: InvokeV3 with ValidResourceBounds::L1Gas(
    ResourceBounds { max_amount: 1000, max_price_per_unit: 5 }
), tip = 0

Rust get_tip_resource_bounds_hash:
  resource_felts = [packed(L1Gas=1000,price=5), packed(L2Gas=0,price=0)]
  hash = poseidon_hash_many(n=3, [0, packed_L1Gas, packed_L2Gas_zero])
       = H_rust

Cairo OS hash_fee_fields (n_resource_bounds hardcoded = 3):
  data_to_hash = [0, packed_L1Gas, packed_L2Gas_zero, packed_L1DataGas_zero]
  hash = poseidon_hash_many(n=4, data_to_hash)
       = H_cairo

H_rust ≠ H_cairo  →  OS hash verification fails for this valid transaction.
``` [5](#0-4) [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L183-197)
```text
    tempvar common_tx_fields = new CommonTxFields(
        tx_hash_prefix=tx_hash_prefix,
        version=3,
        sender_address=sender_address,
        chain_id=block_context.os_global_context.starknet_os_config.chain_id,
        nonce=nonce,
        tip=tip,
        n_resource_bounds=3,
        resource_bounds=resource_bounds,
        paymaster_data_length=paymaster_data_length,
        paymaster_data=paymaster_data,
        nonce_data_availability_mode=nonce_data_availability_mode,
        fee_data_availability_mode=fee_data_availability_mode,
    );
    return common_tx_fields;
```

**File:** crates/starknet_api/src/transaction/fields.rs (L364-366)
```rust
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
```

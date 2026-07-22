### Title
`ValidResourceBounds::AllResources` with zero L2/L1DataGas silently re-classified as `L1Gas` after protobuf round-trip, producing a divergent transaction hash — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` re-classifies an `AllResources` transaction as `L1Gas` whenever `l2_gas` and `l1_data_gas` are both zero. Because `get_tip_resource_bounds_hash` hashes a **different number of resource-bound elements** depending on the variant (3 for `L1Gas`, 4 for `AllResources`), the transaction hash computed by the proposer diverges from the hash recomputed by any node that deserializes the transaction from protobuf. The test suite itself acknowledges this defect with an explicit workaround comment.

---

### Finding Description

**Step 1 – The classification gate in the protobuf deserializer**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` decides the variant purely by inspecting whether the wire values are zero:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

A transaction that was originally `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is therefore re-classified as `L1Gas(X)` after any protobuf round-trip.

**Step 2 – The hash function branches on the variant**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` hashes **2 resource-bound felts** for `L1Gas` (L1_GAS + L2_GAS) but **3 resource-bound felts** for `AllResources` (L1_GAS + L2_GAS + L1_DATA_GAS):

```rust
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];
// For new V3 txs, need to also hash the data gas bounds.
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // ← 2 elements
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // ← 3 elements
    }
});
Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
``` [2](#0-1) 

The Poseidon hash over `[tip, L1_GAS, L2_GAS]` is structurally different from the hash over `[tip, L1_GAS, L2_GAS, L1_DATA_GAS]`, so the two variants always produce distinct digests even when `L2_GAS` and `L1_DATA_GAS` are zero.

**Step 3 – The original hash is computed from `AllResources`**

When a V3 transaction enters the sequencer via the gateway, `InternalRpcInvokeTransactionV3.resource_bounds` is typed as `AllResourceBounds` (never `ValidResourceBounds`). Its `InvokeTransactionV3Trait` implementation always wraps it in `ValidResourceBounds::AllResources`:

```rust
fn resource_bounds(&self) -> ValidResourceBounds {
    ValidResourceBounds::AllResources(self.resource_bounds)
}
``` [3](#0-2) 

The hash stored in `InternalRpcTransaction.tx_hash` is therefore always the **4-element** `AllResources` hash.

**Step 4 – The P2P sync path re-classifies and recomputes**

`DataOrFin<FullTransaction>` is the wire type for P2P transaction sync. `FullTransaction` carries a `Transaction` (which uses `ValidResourceBounds`). When the receiving node deserializes the protobuf bytes, the converter at Step 1 fires and produces `ValidResourceBounds::L1Gas`. Any subsequent call to `calculate_transaction_hash` or `validate_transaction_hash` on the deserialized `Transaction` then uses the **3-element** path, yielding a hash that does not match the stored `tx_hash`.

**Step 5 – The test suite acknowledges the defect**

The consensus protobuf round-trip test explicitly works around this by forcing `l2_gas.max_amount` to a non-zero value:

```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(transactions: &mut [ConsensusTransaction]) {
    ...
    resource_bounds.l2_gas.max_amount = GasAmount(1);
}
``` [4](#0-3) 

The workaround is applied only in tests; production code has no equivalent guard.

---

### Impact Explanation

A V3 transaction with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is accepted by the gateway and sequenced normally. Its canonical hash is the `AllResources` Poseidon digest. After the block is propagated via P2P sync, every receiving node deserializes the transaction as `L1Gas` and recomputes a structurally different hash. `validate_transaction_hash` returns `false`, causing the receiving node to treat the transaction (and potentially the block) as invalid. This matches the impact category: **"Transaction conversion or signature/hash logic binds the wrong hash"** (High).

---

### Likelihood Explanation

Any user can submit a V3 invoke, declare, or deploy-account transaction with `l2_gas.max_amount = 0` and `l1_data_gas.max_amount = 0` (and any prices). The gateway imposes no lower bound on these amounts. The condition is therefore trivially reachable by an unprivileged sender with no special knowledge of the system.

---

### Recommendation

Fix the protobuf deserializer to preserve the original variant rather than inferring it from zero-value fields. The canonical approach is to carry an explicit discriminator field in the protobuf message (e.g., a `bool has_all_resources` flag or a `oneof` variant tag), or to always deserialize into `AllResources` when all three resource-bound fields are present on the wire, regardless of their values.

Alternatively, align `get_tip_resource_bounds_hash` so that both variants hash the same number of elements (always include `L1_DATA_GAS`, using zero when absent), eliminating the hash divergence entirely.

---

### Proof of Concept

1. Submit a V3 invoke transaction with `resource_bounds = { l1_gas: { max_amount: 1, max_price_per_unit: 1 }, l2_gas: { max_amount: 0, max_price_per_unit: 0 }, l1_data_gas: { max_amount: 0, max_price_per_unit: 0 } }`.

2. The gateway accepts it. `convert_rpc_tx_to_internal` stores `tx_hash = H_allresources` (Poseidon of `[tip, L1_GAS_packed, L2_GAS_packed(0), L1_DATA_GAS_packed(0)]`). [5](#0-4) 

3. The transaction is included in a block and serialized to protobuf for P2P sync. The serializer emits `l2_gas = { max_amount: 0, max_price_per_unit: 0 }` and `l1_data_gas = { max_amount: 0, max_price_per_unit: 0 }`. [6](#0-5) 

4. The receiving node deserializes: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas(l1_gas)`. [1](#0-0) 

5. The receiving node calls `get_tip_resource_bounds_hash` with `L1Gas` → hashes `[tip, L1_GAS_packed, L2_GAS_packed(0)]` → `H_l1gas`. [7](#0-6) 

6. `H_l1gas ≠ H_allresources`. `validate_transaction_hash` returns `false` for a legitimately sequenced transaction. [8](#0-7)

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L431-435)
```rust
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
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

**File:** crates/starknet_api/src/transaction_hash.rs (L170-184)
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L637-639)
```rust
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_protobuf/src/converters/consensus_test.rs (L26-47)
```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(transactions: &mut [ConsensusTransaction]) {
    let transaction = &mut transactions[0];
    match transaction {
        ConsensusTransaction::RpcTransaction(rpc_transaction) => match rpc_transaction {
            RpcTransaction::Declare(RpcDeclareTransaction::V3(RpcDeclareTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::Invoke(RpcInvokeTransaction::V3(RpcInvokeTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(
                RpcDeployAccountTransactionV3 { resource_bounds, .. },
            )) => {
                resource_bounds.l2_gas.max_amount = GasAmount(1);
            }
        },
        ConsensusTransaction::L1Handler(_) => {}
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

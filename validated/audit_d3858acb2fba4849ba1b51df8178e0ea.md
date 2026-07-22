### Title
Protobuf `ValidResourceBounds` Conversion Misclassifies `AllResources` V3 Transactions as `L1Gas` When L2 and L1_DATA Gas Are Zero, Causing Transaction Hash Mismatch - (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion uses a zero-value heuristic to distinguish old-format (`L1Gas`, pre-0.13.3) from new-format (`AllResources`, 0.13.3+) transactions. Because `get_tip_resource_bounds_hash` produces structurally different hash preimages for `L1Gas` vs `AllResources` (even when all zero-valued fields are identical), any new-format `AllResources` transaction whose `l2_gas` and `l1_data_gas` are both zero will be silently downgraded to `L1Gas` on the receiving side of a P2P state-sync message. The hash recomputed from the downgraded representation will not match the hash the sequencer computed at ingestion time, breaking hash validation for synced blocks and causing wrong gas-vector computation mode if the transaction is re-executed.

### Finding Description

**Step 1 – Ingestion (gateway path, always `AllResources`)**

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds` (a struct, not an enum). When the gateway converts it to `InternalRpcInvokeTransactionV3`, the trait implementation wraps it unconditionally as `ValidResourceBounds::AllResources(...)`:

```rust
// crates/starknet_api/src/rpc_transaction.rs:636-638
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

`get_invoke_transaction_v3_hash` then calls `get_tip_resource_bounds_hash` with `AllResources`. For `AllResources`, the hash preimage is `[tip, L1_GAS, L2_GAS, L1_DATA_GAS]` (four elements):

```rust
// crates/starknet_api/src/transaction_hash.rs:203-208
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
```

So the stored `tx_hash` = `H(tip, L1_GAS, L2_GAS=0, L1_DATA_GAS=0, …)` — **four resource elements**.

**Step 2 – State-sync deserialization (P2P path, heuristic downgrades to `L1Gas`)**

When a peer receives the block over P2P, the `InvokeTransactionV3` is reconstructed via `TryFrom<protobuf::InvokeV3>`, which calls `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`:

```rust
// crates/apollo_protobuf/src/converters/transaction.rs:417-436
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    ...
    // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
    let l1_data_gas = value.l1_data_gas.unwrap_or_default();
    ...
    Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
        ValidResourceBounds::L1Gas(l1_gas)          // ← downgraded
    } else {
        ValidResourceBounds::AllResources(...)
    })
}
```

When `l2_gas = 0` and `l1_data_gas = 0`, the result is `ValidResourceBounds::L1Gas(l1_gas)`. For `L1Gas`, `get_tip_resource_bounds_hash` produces a preimage of `[tip, L1_GAS, L2_GAS=0]` — **three resource elements** — omitting `L1_DATA_GAS` entirely.

**Step 3 – Hash divergence**

The recomputed hash on the syncing node = `H(tip, L1_GAS, L2_GAS=0, …)` ≠ stored hash `H(tip, L1_GAS, L2_GAS=0, L1_DATA_GAS=0, …)`.

`validate_transaction_hash` checks the recomputed hash against the expected hash from the block:

```rust
// crates/starknet_api/src/transaction_hash.rs:170-184
pub fn validate_transaction_hash(...) -> Result<bool, StarknetApiError> {
    let mut possible_hashes = get_deprecated_transaction_hashes(...)?;
    possible_hashes.push(get_transaction_hash(transaction, chain_id, transaction_options)?);
    Ok(possible_hashes.contains(&expected_hash))
}
```

The recomputed hash is not in `possible_hashes`, so validation returns `false`, and the syncing node rejects the block.

**Step 4 – Wrong execution mode if re-executed**

`ValidResourceBounds::L1Gas` → `GasVectorComputationMode::NoL2Gas`; `ValidResourceBounds::AllResources` → `GasVectorComputationMode::All`. If the blockifier re-executes the transaction (e.g., for proof generation via `blockifier_reexecution`) using the downgraded `L1Gas` bounds, it applies a different fee-charging path, different resource-bound checks, and a different gas vector, producing a divergent execution result from the original.

### Impact Explanation

- **Critical (Wrong state/receipt from blockifier):** Re-execution of a downgraded transaction uses `GasVectorComputationMode::NoL2Gas` instead of `All`, producing different fee charges, different resource-bound validation outcomes, and a different execution trace than the original sequencer run.
- **High (State-sync block rejection):** Any block containing a new-format `AllResources` invoke with `l2_gas = 0` and `l1_data_gas = 0` will fail hash validation on syncing nodes, preventing those nodes from advancing their chain state.

### Likelihood Explanation

The gateway's stateless validator accepts `AllResourceBounds` with any combination of zero values (zero `l2_gas` and `l1_data_gas` is valid). A user who sets only `l1_gas` and leaves the other two at zero — a natural pattern for a transaction that only consumes L1 gas — triggers this path. No special privilege is required; any unprivileged user can submit such a transaction.

### Recommendation

Remove the zero-value heuristic from `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. The protobuf wire format for state-sync already carries all three resource fields; the presence of `l1_data_gas` in the message (even if zero) should unconditionally produce `AllResources`. The `L1Gas` variant should only be produced when the protobuf message is known to originate from a pre-0.13.3 block (e.g., gated by block number or a separate version tag in the message), not inferred from field values:

```rust
// Proposed fix: always AllResources when l1_data_gas field is present
Ok(match value.l1_data_gas {
    None => ValidResourceBounds::L1Gas(l1_gas),   // truly old format: field absent
    Some(_) => ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }),
})
```

### Proof of Concept

1. Submit an invoke V3 transaction via the RPC gateway with:
   ```json
   "resource_bounds": {
     "L1_GAS":      { "max_amount": "0x1000", "max_price_per_unit": "0x1" },
     "L2_GAS":      { "max_amount": "0x0",    "max_price_per_unit": "0x0" },
     "L1_DATA_GAS": { "max_amount": "0x0",    "max_price_per_unit": "0x0" }
   }
   ```
2. The gateway converts to `InternalRpcInvokeTransactionV3` (always `AllResources`) and computes hash **H₁** = `Poseidon(invoke, v3, sender, H(tip, L1_GAS, L2_GAS=0, L1_DATA_GAS=0), …)`.
3. The transaction is included in a block with `tx_hash = H₁`.
4. A syncing peer receives the block via P2P. `TryFrom<protobuf::ResourceBounds>` sees `l2_gas.is_zero() && l1_data_gas.is_zero()` → produces `ValidResourceBounds::L1Gas`.
5. The peer recomputes hash **H₂** = `Poseidon(invoke, v3, sender, H(tip, L1_GAS, L2_GAS=0), …)` — missing the `L1_DATA_GAS` element.
6. **H₁ ≠ H₂**. `validate_transaction_hash` returns `false`; the peer rejects the block.

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

**File:** crates/starknet_api/src/transaction_hash.rs (L187-211)
```rust
// An implementation of the SNIP: https://github.com/EvyatarO/SNIPs/blob/snip-8/SNIPS/snip-8.md
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

**File:** crates/starknet_api/src/transaction_hash.rs (L370-405)
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
}
```

**File:** crates/starknet_api/src/transaction/fields.rs (L363-366)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
```

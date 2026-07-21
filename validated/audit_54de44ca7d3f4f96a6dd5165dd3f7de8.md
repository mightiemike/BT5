### Title
`ValidResourceBounds` Protobuf Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Different Transaction Hash and Wrong Execution Mode — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` implementation uses a value-based heuristic — checking whether `l2_gas` and `l1_data_gas` are both zero — to decide which enum variant to reconstruct. A V3 transaction submitted with `AllResources` bounds where `l2_gas = 0` and `l1_data_gas = 0` is serialized to protobuf with all three fields explicitly present and zero, but is deserialized back as `ValidResourceBounds::L1Gas`. Because `get_tip_resource_bounds_hash` hashes a different number of resource felts for `L1Gas` (2) versus `AllResources` (3), the transaction hash computed from the deserialized form diverges from the hash computed at the gateway. Additionally, `get_gas_vector_computation_mode` returns `NoL2Gas` instead of `All`, causing re-execution to use a different gas accounting path.

### Finding Description

**Serialization path** (`From<ValidResourceBounds> for protobuf::ResourceBounds`):

Both `L1Gas` and `AllResources`-with-zeros serialize to an identical protobuf message — all three `ResourceLimits` fields present, `l2_gas` and `l1_data_gas` both zero. [1](#0-0) 

**Deserialization path** (`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`):

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← wrong for AllResources-with-zeros
} else {
    ValidResourceBounds::AllResources(...)
})
``` [2](#0-1) 

The two variants produce **different hash preimages** in `get_tip_resource_bounds_hash`:

- `L1Gas` → 2 resource felts: `[tip, L1_GAS_concat, L2_GAS_concat]`
- `AllResources` → 3 resource felts: `[tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat]` [3](#0-2) 

The gateway always computes the hash from `InternalRpcTransactionWithoutTxHash`, which carries `AllResourceBounds` (always `AllResources`), so the stored hash uses the 3-felt preimage. [4](#0-3) 

A syncing node that receives the same transaction via P2P state-sync deserializes it as `ValidResourceBounds::L1Gas` and, if it recomputes the hash (e.g., for block verification or re-execution), produces a different value.

The variant also controls `get_gas_vector_computation_mode`:
- `L1Gas` → `NoL2Gas` (only L1 gas tracked)
- `AllResources` → `All` (all three gas types tracked) [5](#0-4) 

This changes which resource bounds are checked during pre-validation and how gas is charged during re-execution. [6](#0-5) 

### Impact Explanation

A syncing node that re-executes a block containing such a transaction (e.g., for proving via `blockifier_reexecution`) will:

1. Compute a **different transaction hash** than the one stored in the block, breaking hash-based integrity checks.
2. Apply **`NoL2Gas` accounting** instead of `All`, meaning L2 gas consumption is not checked against the user's bound. A transaction that should revert (zero L2 gas bound in `All` mode) may instead succeed, producing a divergent state root, receipt, and event log.

This matches the allowed impact: *Wrong state, receipt, event, or revert result from blockifier/execution logic for accepted input* (Critical) and *Transaction conversion or signature/hash logic binds the wrong hash or executable payload* (High).

### Likelihood Explanation

Any user can submit a V3 `invoke` transaction with `AllResourceBounds` where `l2_gas = {0, 0}` and `l1_data_gas = {0, 0}` through the standard RPC gateway. The `RpcInvokeTransactionV3` struct always uses `AllResourceBounds`, so the gateway always computes the hash with the 3-felt preimage. No special privilege is required. The bug is latent in every P2P sync of such a transaction.

### Recommendation

Replace the value-based heuristic with an explicit discriminator. The protobuf `ResourceBounds` message should carry a boolean or enum field indicating whether the sender intended `L1Gas` or `AllResources`. Until the protobuf schema is updated, the deserializer should default to `AllResources` when all three fields are present (regardless of their values), reserving `L1Gas` only for the legacy case where `l1_data_gas` is absent:

```rust
// l1_data_gas absent → legacy L1Gas transaction (pre-0.13.3)
// l1_data_gas present (even if zero) → AllResources transaction
Ok(match value.l1_data_gas {
    None => ValidResourceBounds::L1Gas(l1_gas),
    Some(raw) => {
        let l1_data_gas: ResourceBounds = raw.try_into()?;
        ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
    }
})
```

The corresponding `From<ValidResourceBounds> for protobuf::ResourceBounds` serializer must then emit `l1_data_gas: None` for `L1Gas` (not `Some(zero)`), so the round-trip is bijective. [1](#0-0) 

### Proof of Concept

1. Submit a V3 invoke transaction via RPC with:
   ```json
   "resource_bounds": {
     "l1_gas":      { "max_amount": "0x1", "max_price_per_unit": "0x1" },
     "l2_gas":      { "max_amount": "0x0", "max_price_per_unit": "0x0" },
     "l1_data_gas": { "max_amount": "0x0", "max_price_per_unit": "0x0" }
   }
   ```
   The gateway computes hash H₁ using `AllResources` (3-felt preimage including `L1_DATA_GAS_concat`). [7](#0-6) 

2. The transaction is included in a block and propagated via P2P. The protobuf serializer emits `l1_data_gas: Some(zero)`. [8](#0-7) 

3. A syncing node deserializes the `protobuf::ResourceBounds`. The condition `l1_data_gas.is_zero() && l2_gas.is_zero()` is `true`, so the result is `ValidResourceBounds::L1Gas`. [9](#0-8) 

4. The syncing node recomputes the hash using `get_tip_resource_bounds_hash` with `L1Gas` → 2-felt preimage → hash H₂ ≠ H₁. [10](#0-9) 

5. During re-execution, `get_gas_vector_computation_mode()` returns `NoL2Gas`, bypassing L2 gas bound enforcement that the original sequencer applied, producing a divergent execution result. [5](#0-4)

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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

**File:** crates/starknet_api/src/transaction/fields.rs (L416-421)
```rust
    pub fn get_gas_vector_computation_mode(&self) -> GasVectorComputationMode {
        match self {
            Self::AllResources(_) => GasVectorComputationMode::All,
            Self::L1Gas(_) => GasVectorComputationMode::NoL2Gas,
        }
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L388-425)
```rust
                let resources_amount_tuple = match &context.resource_bounds {
                    ValidResourceBounds::L1Gas(l1_gas_resource_bounds) => vec![(
                        L1Gas,
                        l1_gas_resource_bounds,
                        minimal_gas_amount_vector.to_l1_gas_for_fee(
                            tx_context.get_gas_prices(),
                            &tx_context.block_context.versioned_constants,
                        ),
                        block_info.gas_prices.l1_gas_price(fee_type),
                    )],
                    ValidResourceBounds::AllResources(AllResourceBounds {
                        l1_gas: l1_gas_resource_bounds,
                        l2_gas: l2_gas_resource_bounds,
                        l1_data_gas: l1_data_gas_resource_bounds,
                    }) => {
                        let GasPriceVector { l1_gas_price, l1_data_gas_price, l2_gas_price } =
                            block_info.gas_prices.gas_price_vector(fee_type);
                        vec![
                            (
                                L1Gas,
                                l1_gas_resource_bounds,
                                minimal_gas_amount_vector.l1_gas,
                                *l1_gas_price,
                            ),
                            (
                                L1DataGas,
                                l1_data_gas_resource_bounds,
                                minimal_gas_amount_vector.l1_data_gas,
                                *l1_data_gas_price,
                            ),
                            (
                                L2Gas,
                                l2_gas_resource_bounds,
                                minimal_gas_amount_vector.l2_gas,
                                *l2_gas_price,
                            ),
                        ]
                    }
```

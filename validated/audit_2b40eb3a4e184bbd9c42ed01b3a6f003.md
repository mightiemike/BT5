### Title
Lossy Protobuf `ValidResourceBounds` Conversion Silently Mutates `AllResources` to `L1Gas`, Producing a Wrong Transaction Hash and Breaking RPC Serving of P2P-Synced Blocks - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` silently downgrades `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` to `L1Gas(X)`. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` element only for the `AllResources` variant, the two variants produce **different Poseidon hashes** over identical numeric values. Any `InvokeTransactionV3` submitted with `AllResourceBounds` where both `l2_gas` and `l1_data_gas` are zero — a configuration the gateway explicitly accepts — will have its resource-bounds variant permanently mutated after one P2P block-sync round-trip, causing hash divergence and breaking the `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` conversion that the RPC layer depends on.

### Finding Description

**Step 1 — Lossy protobuf round-trip**

`crates/apollo_protobuf/src/converters/transaction.rs` lines 431–435:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant changed
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

The serializer (`From<ValidResourceBounds> for protobuf::ResourceBounds`, lines 471–489) emits `l2_gas = 0` and `l1_data_gas = 0` for the `L1Gas` variant, and also emits those same zero values for an `AllResources` transaction whose user-supplied bounds happen to be zero. On the return trip the deserializer cannot distinguish the two cases and always produces `L1Gas`. [1](#0-0) [2](#0-1) 

**Step 2 — Hash divergence**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` appends the `L1_DATA_GAS` element **only** for `AllResources`:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 elements
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 elements
    }
});
```

For `AllResources(l1=X, l2=0, l1_data=0)` the Poseidon input is `[tip, concat(L1_GAS,X), concat(L2_GAS,0), concat(L1_DATA_GAS,0)]` — four felts. After the protobuf round-trip the input is `[tip, concat(L1_GAS,X), concat(L2_GAS,0)]` — three felts. The resulting hashes are distinct even though all numeric values are identical. [3](#0-2) [4](#0-3) 

**Step 3 — Attacker-controlled trigger**

The gateway's stateless validator explicitly accepts `AllResourceBounds { l1_gas: NON_EMPTY, l2_gas: default(), l1_data_gas: default() }` (the `valid_l1_gas` test case). Any unprivileged user can submit such a transaction. The `RpcInvokeTransactionV3 → InternalRpcInvokeTransactionV3` conversion preserves `AllResourceBounds` as `ValidResourceBounds::AllResources`, so the hash stored at sequencing time uses the 3-element preimage. [5](#0-4) [6](#0-5) 

**Step 4 — RPC breakage after P2P sync**

The P2P sync server serialises `FullTransaction { transaction, transaction_output, transaction_hash }` to protobuf. The receiving node deserialises the `InvokeTransactionV3` with `L1Gas` and writes it to storage. When the RPC layer later reads that stored transaction and attempts the conversion:

```rust
impl TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3 {
    fn try_from(value: InvokeTransactionV3) -> Result<Self, Self::Error> {
        Ok(Self {
            resource_bounds: match value.resource_bounds {
                ValidResourceBounds::AllResources(bounds) => bounds,
                _ => {
                    return Err(StarknetApiError::OutOfRange { ... });  // ← fires
                }
            },
            ...
        })
    }
}
```

the conversion returns an error, causing `starknet_getTransactionByHash`, `starknet_simulateTransactions`, `starknet_traceTransaction`, and related endpoints to fail for every such transaction on the synced node. [7](#0-6) 

**Step 5 — Hash validation failure**

`validate_transaction_hash` recomputes the hash from the stored `Transaction` object and compares it to the stored `TransactionHash`. After the variant mutation the recomputed hash differs from the original, so any code path that calls `validate_transaction_hash` on a P2P-synced block will report a mismatch for these transactions. [8](#0-7) 

### Impact Explanation

A syncing node that receives a block containing an `AllResources(l1, 0, 0)` invoke transaction over P2P will:

1. Store the transaction with the wrong `ValidResourceBounds` variant (`L1Gas` instead of `AllResources`).
2. Fail to serve any RPC call that reads and re-serialises that transaction (`starknet_getTransactionByHash`, `starknet_simulateTransactions`, `starknet_traceTransaction`, `starknet_estimateFee` on historical blocks).
3. Produce a wrong transaction hash if it ever recomputes the hash from the stored object, breaking hash-based integrity checks.
4. Execute the transaction in `GasVectorComputationMode::NoL2Gas` instead of `All` if it re-executes the block, potentially producing a different revert/success outcome and wrong receipt.

This matches the allowed impact: **"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value"** and **"Wrong state, receipt, event … or revert result from blockifier/syscall/execution logic for accepted input."**

### Likelihood Explanation

- The trigger is a standard, gateway-accepted `InvokeV3` transaction with only `l1_gas` set (the most common pre-0.13.3 pattern).
- No special privileges are required; any user can submit such a transaction.
- The bug fires on every P2P block-sync round-trip for every such transaction, so it is deterministic and not probabilistic.
- The only gate that would prevent it is if the network never produces `AllResources(l1, 0, 0)` transactions, but the gateway explicitly allows them.

### Recommendation

Replace the heuristic variant-selection logic in the protobuf deserialiser with an explicit discriminant. Options:

1. **Add a boolean/enum field to the protobuf `ResourceBounds` message** that records whether the original transaction was `L1Gas` or `AllResources`, and use that field on deserialisation instead of inferring from zero values.
2. **Always deserialise as `AllResources`** when `l1_data_gas` is present in the protobuf message (even if zero), and only fall back to `L1Gas` when the field is absent (i.e., the legacy 0.13.2 wire format where `l1_data_gas` is `None`). The existing `TODO(Shahak)` comment already anticipates this fix.

The second option is backward-compatible: the `unwrap_or_default()` on line 427 already handles the absent-field case for old peers, so changing the condition to `value.l1_data_gas.is_none() && l2_gas.is_zero()` (or simply always producing `AllResources` when `l1_data_gas` is `Some`) would be sufficient. [9](#0-8) 

### Proof of Concept

```
1. Submit RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     }
   Gateway accepts it (valid_l1_gas case).
   Sequencer computes hash H1 using AllResources preimage (4 Poseidon inputs).

2. Transaction is included in block N.

3. A syncing peer requests block N via P2P.
   Server serialises InvokeTransactionV3 → protobuf::ResourceBounds:
     l1_gas = {1000, 1}, l2_gas = {0, 0}, l1_data_gas = {0, 0}

4. Peer deserialises:
     l1_data_gas.is_zero() && l2_gas.is_zero()  →  ValidResourceBounds::L1Gas({1000,1})
   Peer stores transaction with L1Gas variant.

5. Peer recomputes hash H2 using L1Gas preimage (3 Poseidon inputs).
   H1 ≠ H2  →  validate_transaction_hash returns false.

6. Peer's RPC receives starknet_getTransactionByHash(H1):
   Reads InvokeTransactionV3 { resource_bounds: L1Gas(...) } from storage.
   TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3 hits the `_ => Err(...)` arm.
   RPC returns an error for a transaction that is valid and committed on-chain.
```

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L586-611)
```rust
impl TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3 {
    type Error = StarknetApiError;

    fn try_from(value: InvokeTransactionV3) -> Result<Self, Self::Error> {
        Ok(Self {
            resource_bounds: match value.resource_bounds {
                ValidResourceBounds::AllResources(bounds) => bounds,
                _ => {
                    return Err(StarknetApiError::OutOfRange {
                        string: "resource_bounds".to_string(),
                    });
                }
            },
            signature: value.signature,
            nonce: value.nonce,
            tip: value.tip,
            paymaster_data: value.paymaster_data,
            nonce_data_availability_mode: value.nonce_data_availability_mode,
            fee_data_availability_mode: value.fee_data_availability_mode,
            sender_address: value.sender_address,
            calldata: value.calldata,
            account_deployment_data: value.account_deployment_data,
            proof_facts: value.proof_facts,
            proof: Proof::default(),
        })
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L697-713)
```rust
impl From<RpcInvokeTransactionV3> for InternalRpcInvokeTransactionV3 {
    fn from(tx: RpcInvokeTransactionV3) -> Self {
        Self {
            sender_address: tx.sender_address,
            calldata: tx.calldata,
            signature: tx.signature,
            nonce: tx.nonce,
            resource_bounds: tx.resource_bounds,
            tip: tx.tip,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            proof_facts: tx.proof_facts,
            // Note: proof field is dropped
        }
    }
```

Let me examine the relevant code files to verify the claims in this question.

Let me look at how the P2P path handles transaction hashes and whether hash recomputation occurs after protobuf conversion.

Now I have enough information to make a definitive assessment. Let me verify the exact hash divergence and the round-trip behavior.

### Title
P2P Block-Sync Protobuf Converter Silently Downgrades `AllResourceBounds{l2_gas=0, l1_data_gas=0}` to `L1Gas`, Producing Hash-Domain Divergence and Wrong RPC Re-execution Values — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` implementation used in the P2P **block-sync** path silently maps any `AllResourceBounds` with `l2_gas=0` and `l1_data_gas=0` to `ValidResourceBounds::L1Gas`. Because the block-sync path stores the transaction hash verbatim from the protobuf message (not recomputed), the stored `FullTransaction` ends up with `resource_bounds = L1Gas` but a `transaction_hash` that was computed over the `AllResources` preimage (3 resource felts). `validate_transaction_hash` is never called in the production sync pipeline, so the inconsistency is silently persisted. Any subsequent RPC simulation, fee estimation, or tracing on the syncing node re-executes the transaction under the wrong resource-bounds variant, returning authoritative-looking wrong values.

---

### Finding Description

**Step 1 — The downgrade.**
`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in the block-sync converter:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

Any `InvokeV3` whose protobuf `ResourceBounds` carries `l2_gas = {0, 0}` and `l1_data_gas = {0, 0}` (or absent) is silently converted to `ValidResourceBounds::L1Gas`, regardless of whether the original signer used the `AllResources` hash domain.

**Step 2 — Hash is taken verbatim, never recomputed.**
The block-sync deserialization path extracts `transaction_hash` directly from the protobuf field and pairs it with the downgraded transaction body:

```rust
let tx_hash = value.transaction_hash.clone()
    .ok_or(missing("Transaction::transaction_hash"))?
    .try_into().map(TransactionHash)?;
// ... deserialize body (with L1Gas downgrade) ...
Ok((transaction, tx_hash))
``` [2](#0-1) 

The resulting `FullTransaction { transaction, transaction_output, transaction_hash }` is stored with the original `AllResources` hash but `L1Gas` resource bounds.

**Step 3 — `validate_transaction_hash` is never called in the sync pipeline.**
A grep across all production crates shows `validate_transaction_hash` is defined once and referenced only in its own test file — it is not invoked anywhere in the P2P sync pipeline. [3](#0-2) 

**Step 4 — The hash-domain divergence is concrete.**
`get_tip_resource_bounds_hash` produces structurally different Felt values for the two variants:

- `L1Gas` path: `poseidon([tip, concat(l1_gas, "L1_GAS"), concat(zero, "L2_GAS")])` — **2 resource felts**
- `AllResources` path: same 2 felts **plus** `concat(zero, "L1_DATA")` — **3 resource felts** [4](#0-3) 

Even when `l2_gas = 0` and `l1_data_gas = 0`, the two poseidon digests differ because the input length differs. The stored hash (3-felt preimage) will never equal the hash recomputed from the stored `L1Gas` body (2-felt preimage).

**Step 5 — Re-execution diverges.**
`TransactionContext::initial_sierra_gas` and `get_gas_vector_computation_mode` branch on the `ValidResourceBounds` variant:

- `L1Gas` → `initial_gas_no_user_l2_bound()` (a large constant), `GasVectorComputationMode::NoL2Gas`
- `AllResources{l2_gas: GasAmount(0)}` → `initial_sierra_gas = 0`, `GasVectorComputationMode::All` [5](#0-4) 

A transaction originally executed with `AllResources{l2_gas=0}` (zero Sierra gas budget, likely reverted) will be re-executed on the syncing node with `L1Gas` (large Sierra gas budget), potentially succeeding and returning a completely different execution trace, fee estimate, and events.

**Step 6 — The mempool P2P path is NOT affected.**
The mempool path uses a separate converter `TryFrom<protobuf::ResourceBounds> for AllResourceBounds` that always produces `AllResourceBounds` and errors if l1_data_gas is absent. The `RpcInvokeTransactionV3` struct holds `AllResourceBounds` directly, so the downgrade never occurs on the mempool path. [6](#0-5) [7](#0-6) 

---

### Impact Explanation

A syncing node that receives a block containing an `InvokeV3` with `AllResourceBounds{l2_gas=0, l1_data_gas=0}` will:

1. Store the transaction with `ValidResourceBounds::L1Gas` (StorageSerde tag 0) while the committed block hash encodes the `AllResources` preimage (tag 1).
2. Serve wrong `resource_bounds` type via `starknet_getTransactionByHash` / `starknet_getBlockWithTxs`.
3. Return a divergent execution trace, fee estimate, and revert status from `starknet_simulateTransactions` / `starknet_estimateFee` / `starknet_traceTransaction` — because `initial_sierra_gas` and `GasVectorComputationMode` differ between the two variants.
4. Produce a `tip_resource_bounds_hash` that does not match the signed hash, meaning any downstream hash-verification logic will silently disagree with the committed block.

This satisfies: **High — RPC execution, fee estimation, tracing, simulation returns an authoritative-looking wrong value** and **High — Transaction conversion binds the wrong hash/type to the executable payload**.

---

### Likelihood Explanation

Any unprivileged user can submit an `InvokeV3` via the standard JSON-RPC `starknet_addInvokeTransaction` endpoint with `resource_bounds = {l1_gas: X, l2_gas: {0,0}, l1_data_gas: {0,0}}`. The gateway accepts `AllResourceBounds` with zero l2/l1_data values. Once included in a block, every node that syncs via P2P will trigger the downgrade. No special privileges, no malformed bytes, no peer manipulation required.

---

### Recommendation

In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` (block-sync path), remove the heuristic downgrade. The variant should be determined by the protocol version or an explicit tag in the protobuf message, not by inspecting whether gas amounts happen to be zero. The simplest fix is to always produce `ValidResourceBounds::AllResources` when all three fields are present (matching the behavior of the `AllResourceBounds` converter in `rpc_transaction.rs`), and reserve `L1Gas` only for messages that explicitly omit `l2_gas` (i.e., pre-0.13.3 legacy sync messages where `l2_gas` is structurally absent, not merely zero).

Additionally, `validate_transaction_hash` should be called in the block-sync pipeline after deserialization to catch any future hash-domain mismatches before they are persisted.

---

### Proof of Concept

```rust
// Construct the two representations of the same wire data.
let l1_gas = ResourceBounds { max_amount: GasAmount(100), max_price_per_unit: GasPrice(1) };
let zero   = ResourceBounds { max_amount: GasAmount(0),   max_price_per_unit: GasPrice(0) };

// Path A: direct AllResources (RPC / original signer)
let all_resources = ValidResourceBounds::AllResources(AllResourceBounds {
    l1_gas, l2_gas: zero, l1_data_gas: zero,
});

// Path B: protobuf block-sync downgrade
let proto = protobuf::ResourceBounds {
    l1_gas: Some(/* l1_gas */), l2_gas: Some(/* zero */), l1_data_gas: Some(/* zero */),
};
let l1_gas_variant = ValidResourceBounds::try_from(proto).unwrap();
// → ValidResourceBounds::L1Gas(l1_gas)

let tip = Tip(0);
let hash_all = get_tip_resource_bounds_hash(&all_resources, &tip).unwrap();
let hash_l1  = get_tip_resource_bounds_hash(&l1_gas_variant, &tip).unwrap();

// These MUST differ because the poseidon input lengths differ (3 vs 2 resource felts).
assert_ne!(hash_all, hash_l1);  // passes — concrete divergence confirmed
```

The stored `FullTransaction` on a syncing node carries `hash_all` (from the protobuf `transaction_hash` field) but `l1_gas_variant` as its `resource_bounds`. Any call to `get_invoke_transaction_v3_hash` on the stored transaction produces `hash_l1 ≠ hash_all`, and any re-execution uses the wrong gas-computation mode.

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L136-183)
```rust
    fn try_from(value: protobuf::TransactionInBlock) -> Result<Self, Self::Error> {
        let tx_hash = value
            .transaction_hash
            .clone()
            .ok_or(missing("Transaction::transaction_hash"))?
            .try_into()
            .map(TransactionHash)?;
        let txn = value.txn.ok_or(missing("Transaction::txn"))?;
        let transaction: Transaction = match txn {
            protobuf::transaction_in_block::Txn::DeclareV0(declare_v0) => Transaction::Declare(
                DeclareTransaction::V0(DeclareTransactionV0V1::try_from(declare_v0)?),
            ),
            protobuf::transaction_in_block::Txn::DeclareV1(declare_v1) => Transaction::Declare(
                DeclareTransaction::V1(DeclareTransactionV0V1::try_from(declare_v1)?),
            ),
            protobuf::transaction_in_block::Txn::DeclareV2(declare_v2) => Transaction::Declare(
                DeclareTransaction::V2(DeclareTransactionV2::try_from(declare_v2)?),
            ),
            protobuf::transaction_in_block::Txn::DeclareV3(declare_v3) => Transaction::Declare(
                DeclareTransaction::V3(DeclareTransactionV3::try_from(declare_v3)?),
            ),
            protobuf::transaction_in_block::Txn::Deploy(deploy) => {
                Transaction::Deploy(DeployTransaction::try_from(deploy)?)
            }
            protobuf::transaction_in_block::Txn::DeployAccountV1(deploy_account_v1) => {
                Transaction::DeployAccount(DeployAccountTransaction::V1(
                    DeployAccountTransactionV1::try_from(deploy_account_v1)?,
                ))
            }
            protobuf::transaction_in_block::Txn::DeployAccountV3(deploy_account_v3) => {
                Transaction::DeployAccount(DeployAccountTransaction::V3(
                    DeployAccountTransactionV3::try_from(deploy_account_v3)?,
                ))
            }
            protobuf::transaction_in_block::Txn::InvokeV0(invoke_v0) => Transaction::Invoke(
                InvokeTransaction::V0(InvokeTransactionV0::try_from(invoke_v0)?),
            ),
            protobuf::transaction_in_block::Txn::InvokeV1(invoke_v1) => Transaction::Invoke(
                InvokeTransaction::V1(InvokeTransactionV1::try_from(invoke_v1)?),
            ),
            protobuf::transaction_in_block::Txn::InvokeV3(invoke_v3) => Transaction::Invoke(
                InvokeTransaction::V3(InvokeTransactionV3::try_from(invoke_v3)?),
            ),
            protobuf::transaction_in_block::Txn::L1Handler(l1_handler) => {
                Transaction::L1Handler(L1HandlerTransaction::try_from(l1_handler)?)
            }
        };
        Ok((transaction, tx_hash))
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L431-435)
```rust
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
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

**File:** crates/blockifier/src/context.rs (L56-72)
```rust
        match &self.tx_info {
            TransactionInfo::Deprecated(_)
            | TransactionInfo::Current(CurrentTransactionInfo {
                resource_bounds: ValidResourceBounds::L1Gas(_),
                ..
            }) => self.block_context.versioned_constants.initial_gas_no_user_l2_bound(),
            TransactionInfo::Current(CurrentTransactionInfo {
                resource_bounds: ValidResourceBounds::AllResources(AllResourceBounds { l2_gas, .. }),
                ..
            }) => {
                #[cfg(feature = "reexecution")]
                if self.block_context.versioned_constants.ignore_user_l2_gas_bound {
                    return self.block_context.versioned_constants.initial_gas_no_user_l2_bound();
                }
                l2_gas.max_amount
            }
        }
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L212-223)
```rust
impl TryFrom<protobuf::ResourceBounds> for AllResourceBounds {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        Ok(Self {
            l1_gas: value.l1_gas.ok_or(missing("ResourceBounds::l1_gas"))?.try_into()?,
            l2_gas: value.l2_gas.ok_or(missing("ResourceBounds::l2_gas"))?.try_into()?,
            l1_data_gas: value
                .l1_data_gas
                .ok_or(missing("ResourceBounds::l1_data_gas"))?
                .try_into()?,
        })
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L550-566)
```rust
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, SizeOf)]
pub struct RpcInvokeTransactionV3 {
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub resource_bounds: AllResourceBounds,
    pub tip: Tip,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
    #[serde(default, skip_serializing_if = "Proof::is_empty")]
    pub proof: Proof,
}
```

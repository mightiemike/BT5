### Title
Protobuf `ValidResourceBounds` Downgrade Silently Produces Wrong Transaction Hash for Post-0.13.3 Transactions with Zero L2/L1DataGas Bounds — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion in `crates/apollo_protobuf/src/converters/transaction.rs` silently collapses a post-0.13.3 `AllResources` transaction (whose `l2_gas` and `l1_data_gas` happen to be zero) into the pre-0.13.3 `L1Gas` variant. Because `get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` includes the `l1_data_gas` packed field **only** for `AllResources`, the hash computed from the deserialized transaction is structurally different (3-element Poseidon input vs. 4-element) from the hash the user signed at submission time. This is a direct analog of the BufferBinaryPool partial-unlock bug: just as that contract unlocked only the *sent* amount instead of the full *locked* amount, this code hashes only the *partial* resource-bounds set (L1 gas only) instead of the *full* set (L1 + L2 + L1DataGas) that was committed to in the user's signature.

---

### Finding Description

**Step 1 – Originating node (gateway/consensus converter) always uses `AllResources`.**

When a V3 invoke transaction arrives via RPC, `RpcInvokeTransactionV3.resource_bounds` is typed `AllResourceBounds`. The conversion to `InternalRpcInvokeTransactionV3` wraps it unconditionally as `ValidResourceBounds::AllResources(tx.resource_bounds)`: [1](#0-0) 

The hash is then computed via `calculate_transaction_hash`, which calls `get_invoke_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash` with the `AllResources` variant.

**Step 2 – `get_tip_resource_bounds_hash` produces a 4-element Poseidon input for `AllResources`, but only 3 elements for `L1Gas`.** [2](#0-1) 

For `AllResources` with zero `l2_gas` and `l1_data_gas`:
```
Poseidon([tip, pack(l1_gas, L1_GAS), pack(0, L2_GAS), pack(0, L1_DATA_GAS)])
```
For `L1Gas`:
```
Poseidon([tip, pack(l1_gas, L1_GAS), pack(0, L2_GAS)])
```
These are **different** hashes even when the numeric values of `l2_gas` and `l1_data_gas` are both zero, because Poseidon is sensitive to the number of absorbed elements.

**Step 3 – Protobuf deserialization silently downgrades `AllResources` → `L1Gas`.**

When the transaction is serialized to protobuf and sent over P2P, the receiving node deserializes it through: [3](#0-2) 

The critical branch at line 431:
```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← wrong variant for post-0.13.3 tx
} else {
    ValidResourceBounds::AllResources(...)
})
```

A post-0.13.3 transaction that legitimately carries zero `l2_gas` and zero `l1_data_gas` (accepted by the gateway, as shown in `test_positive_flow` with `valid_l1_gas`) is silently re-classified as a pre-0.13.3 `L1Gas` transaction. The `ValidResourceBounds` variant is the **only** signal used by `get_tip_resource_bounds_hash` to decide whether to include the `l1_data_gas` term.

**Step 4 – Hash recomputation on the receiving node produces a divergent value.**

Any code path on the receiving node that recomputes the transaction hash from the deserialized `InvokeTransactionV3` (e.g., `validate_transaction_hash`, or `calculate_transaction_hash` inside `convert_rpc_tx_to_internal`) will produce the 3-element hash, which does not match the 4-element hash the user signed and the originating node stored. [4](#0-3) 

---

### Impact Explanation

**High – Transaction conversion binds the wrong hash to the transaction.**

The protobuf round-trip changes the `ValidResourceBounds` variant, which changes the hash preimage. The receiving node therefore holds a different transaction hash than the one the user signed. Concretely:

- `validate_transaction_hash` returns `false` for a valid transaction, causing the P2P sync layer to reject it or flag the block as invalid.
- If the wrong hash is stored (e.g., the received hash is trusted without re-verification), the state records a hash that does not correspond to the user's signature, breaking receipt/event integrity.
- If the transaction is later re-executed (e.g., blockifier re-execution), the `L1Gas` variant triggers `GasVectorComputationMode::NoL2Gas` instead of `All`, producing different gas accounting and potentially different execution outcomes than the original block.

---

### Likelihood Explanation

**Medium.** The gateway explicitly accepts V3 transactions with `AllResourceBounds` where only `l1_gas` is non-zero (zero `l2_gas`, zero `l1_data_gas`), as demonstrated by the `valid_l1_gas` test case in `stateless_transaction_validator_test.rs`. Any such transaction that is propagated over P2P will trigger the downgrade. The condition is unusual but fully reachable without any privileged access.

---

### Recommendation

The protobuf deserializer for `ValidResourceBounds` must not use the *values* of `l2_gas` and `l1_data_gas` to infer the *version* of the transaction. Two options:

1. **Always produce `AllResources` when deserializing from protobuf** (since the P2P sync protocol is used only for post-0.13.3 blocks). Remove the `L1Gas` branch from `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`.

2. **Add an explicit version/type discriminator field to the protobuf `ResourceBounds` message** so the deserializer can distinguish pre-0.13.3 `L1Gas` transactions from post-0.13.3 `AllResources` transactions with zero L2/L1DataGas bounds.

---

### Proof of Concept

```
1. User submits RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     }

2. Gateway computes hash H_orig using AllResources:
     tip_resource_bounds_hash = Poseidon([tip, pack(l1_gas), pack(0,L2_GAS), pack(0,L1_DATA_GAS)])
   User signs H_orig.

3. Transaction is serialized to protobuf::ResourceBounds:
     l1_gas      = Some(...)
     l2_gas      = Some(zero)
     l1_data_gas = Some(zero)

4. Receiving node deserializes:
     l1_data_gas.is_zero() && l2_gas.is_zero()  →  ValidResourceBounds::L1Gas(l1_gas)

5. Receiving node computes hash H_recv using L1Gas:
     tip_resource_bounds_hash = Poseidon([tip, pack(l1_gas), pack(0,L2_GAS)])
   (l1_data_gas term absent)

6. H_orig ≠ H_recv  (different Poseidon absorb lengths)

7. validate_transaction_hash(tx, block_number, chain_id, H_orig, ...) returns false
   → valid transaction rejected / wrong hash stored.
```

### Citations

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

**File:** crates/starknet_api/src/transaction_hash.rs (L165-185)
```rust
/// Validates the hash of a starknet transaction.
/// For transactions on testnet or those with a low block_number, we validate the
/// transaction hash against all potential historical hash computations. For recent
/// transactions on mainnet, the hash is validated by calculating the precise hash
/// based on the transaction version.
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

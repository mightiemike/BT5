### Title
`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` silently converts `AllResources` to `L1Gas` when l2/l1_data gas are zero, causing hash domain mismatch - (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserialization of `ValidResourceBounds` uses a zero-value heuristic to select the variant. When a transaction is admitted by the gateway as `AllResources` with zero `l2_gas` and `l1_data_gas`, the protobuf round-trip silently converts it to `L1Gas`. Because `get_tip_resource_bounds_hash` produces structurally different poseidon preimages for the two variants (4 elements vs 3 elements), the recomputed hash after deserialization diverges from the hash computed at admission, breaking hash canonicalization across the public-to-internal conversion boundary.

---

### Finding Description

**Step 1 — Gateway admission uses the combined resource bounds sum.**

`StatelessTransactionValidator::validate_resource_bounds` checks:

```rust
if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0) {
    return Err(ZeroResourceBounds);
}
``` [1](#0-0) 

`max_possible_fee` for `AllResources` sums all three resources:

```rust
l1_gas.max_amount.saturating_mul(l1_gas.max_price_per_unit)
    .saturating_add(l2_gas.max_amount.saturating_mul(...))
    .saturating_add(l1_data_gas.max_amount.saturating_mul(...))
``` [2](#0-1) 

A transaction with `AllResourceBounds { l1_gas: {max_amount: N, max_price: P}, l2_gas: zero, l1_data_gas: zero }` passes this check (combined fee = N×P > 0).

**Step 2 — Hash is computed using the `AllResources` path (4-element poseidon).**

`get_tip_resource_bounds_hash` for `AllResources` appends the `L1_DATA_GAS` element:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
``` [3](#0-2) 

For `AllResources`, the poseidon input is `[tip, L1_GAS_packed, L2_GAS_zero, L1_DATA_GAS_zero]` — **4 elements**.
For `L1Gas`, the poseidon input is `[tip, L1_GAS_packed, L2_GAS_zero]` — **3 elements**.

These produce different hashes even when the zero-valued fields are numerically identical, because poseidon is sensitive to the number of inputs.

**Step 3 — Protobuf deserialization silently changes the variant.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` applies a zero-value heuristic:

```rust
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [4](#0-3) 

A transaction serialized as `AllResources { l1_gas: non-zero, l2_gas: zero, l1_data_gas: zero }` is deserialized as `ValidResourceBounds::L1Gas`. The variant has changed.

**Step 4 — Hash recomputed from deserialized data diverges.**

Any code path that recomputes the transaction hash from the deserialized `ValidResourceBounds` (e.g., P2P sync verification, RPC hash reporting) will call `get_tip_resource_bounds_hash` with `L1Gas`, producing the 3-element hash. This diverges from the 4-element hash computed at admission and stored in `InternalRpcTransaction::tx_hash`. [5](#0-4) 

The OS Cairo `hash_fee_fields` always asserts `n_resource_bounds = 3` and hashes all three resources, confirming that the canonical on-chain hash for V3 transactions always includes the `L1_DATA_GAS` element: [6](#0-5) 

The Rust `L1Gas` path omits `L1_DATA_GAS` from the hash, diverging from the OS canonical form for any transaction that was originally `AllResources`.

---

### Impact Explanation

**High. Transaction conversion or signature/hash logic binds the wrong hash or executable payload.**

A transaction admitted by the gateway with `AllResources` (zero l2/l1_data gas) has its hash computed over 4 poseidon elements. After protobuf round-trip (P2P propagation, consensus message, or storage read-back), the variant becomes `L1Gas` and the hash is recomputed over 3 elements. The two hashes are distinct. Any node that verifies the transaction hash after deserialization will reject the transaction or the block containing it, causing a chain split or silent wrong-hash in RPC receipts.

---

### Likelihood Explanation

A user can deliberately submit a V3 invoke or deploy-account transaction with `AllResourceBounds { l1_gas: {max_amount: X, max_price: Y}, l2_gas: {0,0}, l1_data_gas: {0,0} }`. The gateway's combined-sum check passes (fee = X×Y > 0). No other stateless check rejects it. The transaction is admitted, included in a block, and the divergence is triggered on every subsequent protobuf deserialization of that transaction.

---

### Recommendation

Remove the zero-value heuristic from `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. For V3 transactions, always deserialize as `AllResources` when all three fields are present in the protobuf message. The `L1Gas` variant should only be reconstructed when the protobuf message explicitly signals a pre-0.13.3 transaction (e.g., via a version field or absent `l1_data_gas` field), not by inspecting numeric values.

```rust
// Instead of:
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(...)
})

// Use:
Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))
// and handle L1Gas variant only when l1_data_gas field is absent (pre-0.13.3 wire format)
```

---

### Proof of Concept

```
1. Attacker submits RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      { max_amount: 1_000_000, max_price_per_unit: 1 },
         l2_gas:      { max_amount: 0,         max_price_per_unit: 0 },
         l1_data_gas: { max_amount: 0,         max_price_per_unit: 0 },
     }

2. StatelessTransactionValidator::validate_resource_bounds:
     max_possible_fee(AllResources, tip=0) = 1_000_000 * 1 = 1_000_000 ≠ 0  → PASS

3. convert_rpc_tx_to_internal computes tx_hash via get_invoke_transaction_v3_hash:
     tip_resource_bounds_hash = poseidon(tip, L1_GAS_packed, L2_GAS_zero, L1_DATA_GAS_zero)
                                                                           ^^^^^^^^^^^^^^^^
                                                                           4th element present
     tx_hash_A = poseidon(INVOKE, version, sender, tip_resource_bounds_hash, ...)

4. Transaction propagated via P2P as protobuf::MempoolTransaction.
   Serialized resource_bounds: l1_gas=non-zero, l2_gas=zero, l1_data_gas=zero.

5. Receiving node deserializes via TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
     l1_data_gas.is_zero() && l2_gas.is_zero()  → ValidResourceBounds::L1Gas(l1_gas)
                                                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                                    variant changed

6. Receiving node recomputes hash:
     tip_resource_bounds_hash = poseidon(tip, L1_GAS_packed, L2_GAS_zero)
                                                              ^^^^^^^^^^^
                                                              only 3 elements
     tx_hash_B = poseidon(INVOKE, version, sender, tip_resource_bounds_hash, ...)

7. tx_hash_A ≠ tx_hash_B  →  hash mismatch  →  transaction/block rejected.
```

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L64-69)
```rust
        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }
```

**File:** crates/starknet_api/src/transaction/fields.rs (L398-413)
```rust
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => l1_gas
                .max_amount
                .saturating_mul(l1_gas.max_price_per_unit)
                .saturating_add(
                    l2_gas
                        .max_amount
                        .saturating_mul(l2_gas.max_price_per_unit.saturating_add(tip.into())),
                )
                .saturating_add(
                    l1_data_gas.max_amount.saturating_mul(l1_data_gas.max_price_per_unit),
                ),
        }
```

**File:** crates/starknet_api/src/transaction_hash.rs (L203-210)
```rust
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L119-144)
```text
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

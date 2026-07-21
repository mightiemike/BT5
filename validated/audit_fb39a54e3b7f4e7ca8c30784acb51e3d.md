### Title
Protobuf `ValidResourceBounds` Deserialization Uses Value-Heuristic to Reconstruct Version Variant, Causing Wrong Transaction Hash for `AllResources` Transactions with Zero L2/L1DataGas — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` reconstructs the Rust enum variant (`L1Gas` vs `AllResources`) by inspecting whether the decoded `l2_gas` and `l1_data_gas` values are both zero. An `AllResources` transaction (Starknet ≥ 0.13.3) whose `l2_gas` and `l1_data_gas` bounds happen to be zero is silently downgraded to `ValidResourceBounds::L1Gas`. The `L1Gas` variant causes `get_tip_resource_bounds_hash` to omit the `L1_DATA_GAS` field from the hash preimage, producing a different transaction hash than the one the sender signed. The sequencer then stores and executes the transaction under the wrong hash, binding the wrong hash to the signer's signature.

---

### Finding Description

**Protobuf deserialization heuristic (`crates/apollo_protobuf/src/converters/transaction.rs`, lines 417–436):**

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)   // ← wrong variant for 0.13.3+ tx
        } else {
            ValidResourceBounds::AllResources(...)
        })
    }
}
```

The protobuf wire format carries no version tag for the `ValidResourceBounds` variant. The deserializer uses a value heuristic: if both `l2_gas` and `l1_data_gas` are zero, it reconstructs `L1Gas`; otherwise `AllResources`. This is intentionally left as a TODO for backward compatibility with Starknet 0.13.2.

**Hash preimage divergence (`crates/starknet_api/src/transaction_hash.rs`, lines 188–211):**

```rust
pub fn get_tip_resource_bounds_hash(resource_bounds: &ValidResourceBounds, tip: &Tip) -> ... {
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],                          // ← L1_DATA_GAS omitted
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });
    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
```

When the variant is `L1Gas`, the `L1_DATA_GAS` field is not included in the Poseidon hash. When the variant is `AllResources`, it is included (even if zero). The two produce different hash values even when all numeric fields are identical.

**The invariant that breaks:** A Starknet ≥ 0.13.3 `InvokeTransactionV3` always carries `AllResources` bounds. The sender signs `hash(... | poseidon(tip, L1_GAS_packed, L2_GAS_packed, L1_DATA_GAS_packed) | ...)`. If the transaction is relayed over P2P and deserialized with the heuristic above, and both `l2_gas` and `l1_data_gas` happen to be zero (e.g., a transaction that only uses L1 gas but was submitted as a 0.13.3 `AllResources` tx), the reconstructed variant is `L1Gas`, the hash is recomputed without `L1_DATA_GAS`, and the stored `tx_hash` diverges from the signed hash.

---

### Impact Explanation

**Impact: High — Transaction conversion or signature/hash logic binds the wrong hash.**

The `tx_hash` stored in `InternalRpcTransaction` is computed from the deserialized `ValidResourceBounds` variant. If the variant is wrong, the stored hash differs from the hash the account signed. The blockifier then executes the transaction with the wrong `transaction_hash` in `tx_info`, which is what the account's `__validate__` entry point reads via `get_execution_info`. An account that checks `tx_info.transaction_hash` against an expected value will behave incorrectly. More critically, the sequencer's receipt, event, and block commitment all reference the wrong hash, breaking the canonical transaction identity.

---

### Likelihood Explanation

**Likelihood: Medium.**

The trigger condition — an `AllResources` transaction with both `l2_gas.max_amount == 0 && l2_gas.max_price_per_unit == 0 && l1_data_gas.max_amount == 0 && l1_data_gas.max_price_per_unit == 0` — is reachable. Transactions that only consume L1 gas (e.g., simple ETH transfers on a non-KZG-DA network) may legitimately set both to zero while still being submitted as `AllResources` (0.13.3+) transactions. The path is triggered whenever such a transaction is received over the P2P sync or consensus protobuf channel.

---

### Recommendation

Replace the value-based heuristic with an explicit version/variant tag in the protobuf message, or preserve the variant identity through a separate boolean/enum field. Until the wire format is updated, the deserializer should default to `AllResources` when `l1_data_gas` is absent (legacy 0.13.2 path) but must not downgrade to `L1Gas` when `l1_data_gas` is present and zero. The TODO comment at line 426 acknowledges this:

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
```

The fix is to assert `l1_data_gas` is `Some` for all post-0.13.2 transactions and always produce `AllResources` when `l1_data_gas` is present, regardless of its value:

```rust
let l1_data_gas = value.l1_data_gas;
Ok(match l1_data_gas {
    None => ValidResourceBounds::L1Gas(l1_gas),   // legacy 0.13.2
    Some(l1_data_gas) => ValidResourceBounds::AllResources(AllResourceBounds {
        l1_gas,
        l2_gas,
        l1_data_gas: l1_data_gas.try_into()?,
    }),
})
```

---

### Proof of Concept

1. Construct a Starknet 0.13.3 `InvokeTransactionV3` with `AllResources` bounds where `l2_gas = {max_amount: 0, max_price_per_unit: 0}` and `l1_data_gas = {max_amount: 0, max_price_per_unit: 0}`.
2. Compute the canonical hash using `get_invoke_transaction_v3_hash` with `ValidResourceBounds::AllResources(...)` — this includes `L1_DATA_GAS` in the Poseidon preimage.
3. Serialize the transaction to protobuf via `From<ValidResourceBounds> for protobuf::ResourceBounds` (line 471): this correctly emits `l1_data_gas = Some(zero)`.
4. Deserialize via `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` (line 417): since `l1_data_gas.is_zero() && l2_gas.is_zero()`, the result is `ValidResourceBounds::L1Gas(l1_gas)`.
5. Recompute the hash with the deserialized `L1Gas` variant — `L1_DATA_GAS` is omitted from the Poseidon preimage.
6. The two hashes differ. The sequencer stores and executes the transaction under the hash from step 5, which does not match the hash the account signed in step 2.

**Relevant code locations:**
- Heuristic deserialization: [1](#0-0) 
- Hash preimage branch on variant: [2](#0-1) 
- `ValidResourceBounds` enum definition: [3](#0-2) 
- `get_invoke_transaction_v3_hash` using the variant: [4](#0-3) 
- Serialization (correctly emits `l1_data_gas = Some(zero)` for `L1Gas` variant): [5](#0-4)

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

**File:** crates/starknet_api/src/transaction/fields.rs (L363-367)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}
```

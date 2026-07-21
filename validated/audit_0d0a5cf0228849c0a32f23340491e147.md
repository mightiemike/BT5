### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Divergent Transaction Hash — (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion in the P2P sync protobuf layer silently collapses a `ValidResourceBounds::AllResources` variant (with `l2_gas = 0` and `l1_data_gas = 0`) into `ValidResourceBounds::L1Gas`. Because `get_tip_resource_bounds_hash` hashes a **different number of elements** depending on the variant, the transaction hash computed after protobuf round-trip differs from the hash computed at submission time. Any path that recomputes the transaction hash from the deserialized `Transaction` object — including `validate_transaction_hash` in the P2P sync path — will produce a hash that does not match the one committed in the block, causing the block to be rejected by syncing nodes.

---

### Finding Description

**Step 1 — Hash domain split by variant.**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` hashes a variable-length sequence:

```
L1Gas      → Poseidon(tip, L1_GAS_packed, L2_GAS_packed_zero)          // 3 elements
AllResources → Poseidon(tip, L1_GAS_packed, L2_GAS_packed_zero, L1_DATA_GAS_packed_zero) // 4 elements
```

Even when `l2_gas = 0` and `l1_data_gas = 0`, the two variants produce **different Poseidon digests** because the input length differs. [1](#0-0) 

**Step 2 — Protobuf deserialization collapses the variant.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in the P2P sync converter applies the following rule:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

A transaction that was originally submitted as `AllResources` with `l2_gas = 0` and `l1_data_gas = 0` is silently re-classified as `L1Gas` after protobuf round-trip. The comment acknowledges this is a backward-compat workaround for pre-0.13.3 transactions, but it is applied unconditionally to all versions. [2](#0-1) 

**Step 3 — Submission path always uses `AllResources`.**

`InternalRpcInvokeTransactionV3` stores `resource_bounds: AllResourceBounds` and its `InvokeTransactionV3Trait` implementation always returns `ValidResourceBounds::AllResources(...)`. The gateway therefore always computes the hash with the 4-element preimage. [3](#0-2) 

**Step 4 — Hash divergence in the sync path.**

`get_invoke_transaction_v3_hash` calls `get_tip_resource_bounds_hash` with whatever `ValidResourceBounds` the deserialized `InvokeTransactionV3` carries. After protobuf round-trip the variant is `L1Gas`, so the hash is computed over 3 elements instead of 4, producing hash H2 ≠ H1. [4](#0-3) 

`validate_transaction_hash` recomputes the hash from the deserialized `Transaction` and checks it against the expected value. For V3 transactions there are no deprecated fallback hashes, so the check fails unconditionally. [5](#0-4) 

---

### Impact Explanation

A syncing node that receives a block containing a V3 invoke transaction with `AllResourceBounds{l1_gas > 0, l2_gas = 0, l1_data_gas = 0}` will:

1. Deserialize the transaction via protobuf as `ValidResourceBounds::L1Gas`.
2. Recompute the transaction hash using the 3-element preimage.
3. Obtain H2 ≠ H1 (the hash committed in the block).
4. Fail `validate_transaction_hash`, causing the block to be rejected.

This maps to the allowed impact: **High — RPC/sync admission rejects valid transactions; wrong state or receipt from accepted input.**

---

### Likelihood Explanation

The gateway's stateless validator explicitly accepts V3 transactions where only `l1_gas` is non-zero (test case `valid_l1_gas`): [6](#0-5) 

Any user paying a non-zero L1 gas fee with zero L2 and L1-data-gas bounds triggers the condition. No special privilege is required.

---

### Recommendation

1. **Encode the variant explicitly in the protobuf wire format.** Add a boolean or enum field (e.g., `is_all_resources`) to `ResourceBounds` so the deserializer can reconstruct the correct variant without inspecting field values.
2. **Alternatively**, remove the value-based heuristic and always deserialize post-0.13.3 transactions as `AllResources`, relying on the transaction version field to gate the old `L1Gas` path.
3. **Short-term**: add a regression test that round-trips a `ValidResourceBounds::AllResources{l1_gas > 0, l2_gas = 0, l1_data_gas = 0}` through protobuf and asserts the transaction hash is preserved.

---

### Proof of Concept

```
1. Construct an RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      ResourceBounds { max_amount: 1, max_price_per_unit: 1 },
         l2_gas:      ResourceBounds::default(),   // zero
         l1_data_gas: ResourceBounds::default(),   // zero
     }

2. Compute H1 = get_invoke_transaction_v3_hash(tx, chain_id, version)
   → get_tip_resource_bounds_hash uses AllResources → 4-element Poseidon input
   → H1 = Poseidon(tip, L1_GAS_packed, L2_GAS_zero, L1_DATA_GAS_zero)

3. Serialize tx to protobuf::ResourceBounds (all three fields present, l2_gas and l1_data_gas are zero).

4. Deserialize via TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
   → l1_data_gas.is_zero() && l2_gas.is_zero() == true
   → result = ValidResourceBounds::L1Gas(l1_gas)

5. Compute H2 = get_invoke_transaction_v3_hash(deserialized_tx, chain_id, version)
   → get_tip_resource_bounds_hash uses L1Gas → 3-element Poseidon input
   → H2 = Poseidon(tip, L1_GAS_packed, L2_GAS_zero)

6. Assert H1 != H2  ← divergence confirmed

7. validate_transaction_hash(deserialized_tx, block_number, chain_id, H1, options)
   → recomputes H2, checks H2 ∈ {H1} → false → sync node rejects the block.
```

### Citations

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L69-82)
```rust
#[rstest]
#[case::valid_l1_gas(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l1_gas: NON_EMPTY_RESOURCE_BOUNDS,
            ..Default::default()
        },
        ..Default::default()
    }
)]
```

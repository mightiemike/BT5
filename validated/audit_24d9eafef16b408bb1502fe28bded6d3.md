### Title
`ValidResourceBounds` Protobuf Deserializer Silently Downgrades `AllResources` to `L1Gas`, Producing a Different Transaction Hash - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` uses a zero-value heuristic to decide whether a V3 transaction carries `AllResources` or `L1Gas` bounds. When a user submits a V3 invoke with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`, the gateway computes the transaction hash over four Poseidon elements (tip + L1 + L2 + L1_data). After the transaction is serialised to protobuf and deserialised on any peer or syncing node, the same resource-bounds message is decoded as `ValidResourceBounds::L1Gas(X)`, and `get_tip_resource_bounds_hash` now hashes only three elements (tip + L1 + L2). The two Poseidon digests are structurally different, so the recomputed hash never matches the original, breaking `validate_transaction_hash` and any downstream hash-dependent logic.

---

### Finding Description

**Step 1 – Gateway accepts the triggering transaction.**

The stateless validator accepts `AllResourceBounds` with only `l1_gas` set: [1](#0-0) 

**Step 2 – Hash is computed over four elements for `AllResources`.**

`get_tip_resource_bounds_hash` always appends the L1-data-gas element when the variant is `AllResources`, even when its value is zero: [2](#0-1) 

For `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` the hash input is:
`Poseidon(tip, packed_L1, packed_L2_zero, packed_L1data_zero)` — **4 elements**.

For `L1Gas { l1_gas: X }` the hash input is:
`Poseidon(tip, packed_L1, packed_L2_zero)` — **3 elements**.

These produce distinct field elements.

**Step 3 – Protobuf round-trip silently changes the variant.**

When the transaction is received over P2P or read back from storage, the protobuf converter applies a zero-value heuristic: [3](#0-2) 

Because `l1_data_gas` defaults to zero when absent (`unwrap_or_default`) and both `l2_gas` and `l1_data_gas` are zero, the branch at line 431 fires and the transaction is reconstructed as `ValidResourceBounds::L1Gas(X)` — a structurally different type from the original `AllResources`.

**Step 4 – Hash recomputation diverges.**

`validate_transaction_hash` recomputes the hash from the deserialised `Transaction` fields and compares it to the stored hash: [4](#0-3) 

Because `InvokeTransactionV3.resource_bounds` is now `L1Gas` instead of `AllResources`, `get_tip_resource_bounds_hash` produces the 3-element digest, which never equals the 4-element digest stored at submission time. Validation fails.

The `InvokeTransactionV3` struct that carries `ValidResourceBounds` (used in the storage/API layer) is distinct from `InternalRpcInvokeTransactionV3` which carries `AllResourceBounds` (used in the gateway/consensus layer): [5](#0-4) 

The strict `AllResourceBounds` protobuf converter (used for `InternalRpcTransaction`) is not affected; only the `ValidResourceBounds` converter used for the `Transaction` storage/sync type is vulnerable: [6](#0-5) 

---

### Impact Explanation

Any syncing node or RPC node that deserialises a V3 invoke transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` from protobuf will reconstruct it as `L1Gas(X)`. Every subsequent call to `calculate_transaction_hash` or `validate_transaction_hash` on that object produces a hash that differs from the hash committed to the block. This causes:

- `validate_transaction_hash` to return `false` for a legitimately included transaction, breaking sync.
- Any RPC endpoint that recomputes the hash from stored fields (e.g., `starknet_getTransactionByHash`, trace APIs) to return an authoritative-looking but wrong transaction hash.
- The wrong executable payload being bound to the wrong hash in the blockifier's execution path if the deserialized `Transaction` is used to drive re-execution.

**Matching impact**: *High — Transaction conversion or signature/hash logic binds the wrong hash/type.*

---

### Likelihood Explanation

The gateway explicitly accepts `AllResourceBounds` with only `l1_gas` set (L2 and L1-data-gas zero), as confirmed by the passing test case `valid_l1_gas`. Any user who submits such a transaction — intentionally or by using a wallet that omits optional gas fields — triggers the inconsistency on every peer that processes the block. No privileged access is required.

---

### Recommendation

Remove the zero-value heuristic from `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. Instead, use an explicit discriminant field in the protobuf message (e.g., a `resource_bounds_version` or `is_all_resources` flag) to distinguish pre-0.13.3 `L1Gas` transactions from post-0.13.3 `AllResources` transactions. Until the wire format is updated, the deserialiser should default to `AllResources` (not `L1Gas`) when all three fields are present in the protobuf message, reserving `L1Gas` only for messages that structurally lack the `l2_gas` and `l1_data_gas` fields entirely (i.e., `None`, not zero).

---

### Proof of Concept

1. Craft a V3 invoke transaction with `resource_bounds = AllResourceBounds { l1_gas: ResourceBounds { max_amount: 1000, max_price_per_unit: 1 }, l2_gas: ResourceBounds::default(), l1_data_gas: ResourceBounds::default() }`.
2. Submit via the gateway. The gateway accepts it (matches the `valid_l1_gas` test case). The hash `H_orig` is computed via `get_tip_resource_bounds_hash` with `AllResources` → 4-element Poseidon.
3. Serialise the transaction to protobuf (as done in P2P propagation).
4. Deserialise on a peer using `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. Because `l2_gas.is_zero() && l1_data_gas.is_zero()`, the result is `ValidResourceBounds::L1Gas(l1_gas)`.
5. Call `calculate_transaction_hash` on the deserialised `InvokeTransactionV3`. The hash `H_new` is computed via `get_tip_resource_bounds_hash` with `L1Gas` → 3-element Poseidon.
6. Assert `H_orig != H_new`. Call `validate_transaction_hash(tx, block_number, chain_id, H_orig, options)` → returns `false`, rejecting a valid transaction.

### Citations

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

**File:** crates/starknet_api/src/transaction.rs (L663-688)
```rust
/// An invoke V3 transaction.
#[derive(Debug, Clone, Eq, PartialEq, Hash, Deserialize, Serialize, PartialOrd, Ord)]
pub struct InvokeTransactionV3 {
    pub resource_bounds: ValidResourceBounds,
    pub tip: Tip,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
}

impl TransactionHasher for InvokeTransactionV3 {
    fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
        transaction_version: &TransactionVersion,
    ) -> Result<TransactionHash, StarknetApiError> {
        get_invoke_transaction_v3_hash(self, chain_id, transaction_version)
    }
}
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L212-224)
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
}
```

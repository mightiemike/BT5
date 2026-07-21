### Title
`ValidResourceBounds::AllResources` silently degrades to `L1Gas` after protobuf round-trip, producing a divergent transaction hash preimage — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserialization of `ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` maps any `ResourceBounds` message with zero `l1_data_gas` and zero `l2_gas` to `ValidResourceBounds::L1Gas`, even when the original transaction used `ValidResourceBounds::AllResources`. Because `get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` includes `l1_data_gas` in the hash preimage only for the `AllResources` variant (3 resource entries) and omits it for `L1Gas` (2 resource entries), the recomputed hash after a protobuf round-trip diverges from the original signed hash. Any node that recomputes the transaction hash after deserializing such a transaction from a P2P sync message will produce a wrong hash.

---

### Finding Description

**Root cause — serialization collision:**

`From<ValidResourceBounds> for protobuf::ResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` serializes both `L1Gas` and `AllResources{l1_data_gas:0, l2_gas:0}` to identical protobuf bytes:

```
l1_gas: Some(l1_gas), l2_gas: Some(zero), l1_data_gas: Some(zero)
``` [1](#0-0) 

**Root cause — lossy deserialization:**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` then maps any message where `l1_data_gas.is_zero() && l2_gas.is_zero()` back to `ValidResourceBounds::L1Gas`, discarding the original `AllResources` variant:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← wrong for post-0.13.3 AllResources txs
} else {
    ValidResourceBounds::AllResources(...)
})
``` [2](#0-1) 

**Hash preimage divergence:**

`get_tip_resource_bounds_hash` produces structurally different hash inputs depending on the variant:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 entries
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 entries
    }
});
``` [3](#0-2) 

For `AllResources{l1_data_gas:0, l2_gas:0}`:
- **Original hash**: `poseidon(tip, l1_gas_concat, zero_l2_gas_concat, zero_l1_data_gas_concat)` — 3 elements
- **Post-round-trip hash**: `poseidon(tip, l1_gas_concat, zero_l2_gas_concat)` — 2 elements

These are distinct Poseidon outputs. The hash is used in `get_invoke_transaction_v3_hash`, `get_declare_transaction_v3_hash`, and `get_deploy_account_transaction_v3_hash`: [4](#0-3) 

**Affected conversion path:**

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter is used when deserializing `TransactionInBlock` messages during P2P block sync. A V3 `InvokeTransactionV3` (which always uses `ValidResourceBounds::AllResources` when created from an `RpcInvokeTransactionV3`) with zero `l1_data_gas` and zero `l2_gas` is a valid post-0.13.3 transaction. After protobuf round-trip it becomes `L1Gas`, and any subsequent call to `calculate_transaction_hash` or `validate_transaction_hash` produces the wrong hash. [5](#0-4) 

The `InvokeTransactionV3` struct stores `resource_bounds: ValidResourceBounds`, and the `From<RpcInvokeTransactionV3>` conversion always wraps `AllResourceBounds` in `ValidResourceBounds::AllResources`: [6](#0-5) 

Note: the consensus P2P path is **not** affected because `ConsensusTransaction` uses `RpcTransaction` which carries `AllResourceBounds` directly, and its protobuf converter uses `TryFrom<protobuf::ResourceBounds> for AllResourceBounds` (in `rpc_transaction.rs`), which always produces `AllResourceBounds`: [7](#0-6) 

The affected path is the `TransactionInBlock` sync converter in `transaction.rs`.

---

### Impact Explanation

**High. Transaction conversion or signature/hash logic binds the wrong hash or executable payload.**

A syncing node that receives a `TransactionInBlock` containing a V3 transaction with `AllResources{l1_data_gas:0, l2_gas:0}` will:

1. Deserialize `ValidResourceBounds` as `L1Gas` instead of `AllResources`.
2. Recompute the transaction hash using a 2-element resource preimage instead of the correct 3-element preimage.
3. Produce a hash that does not match the original signed hash.

Consequences:
- `validate_transaction_hash` fails for a valid transaction, causing the syncing node to reject a valid historical block.
- The wrong `ValidResourceBounds` variant causes `get_gas_vector_computation_mode()` to return `NoL2Gas` instead of `All`, corrupting gas accounting in blockifier re-execution.
- Any RPC endpoint that recomputes or serves the transaction hash (e.g., `starknet_getTransactionByHash`, fee estimation, tracing) returns an authoritative-looking wrong value.

---

### Likelihood Explanation

A V3 invoke transaction with `AllResourceBounds` where only `l1_gas` is non-zero (and both `l2_gas` and `l1_data_gas` are zero) is a valid, well-formed transaction accepted by the gateway. The gateway stateless validator only requires at least one non-zero resource bound. Such transactions are routinely submitted by wallets that do not yet set L2 or data gas bounds. Every such transaction that is included in a block and later synced via P2P will trigger this bug.

---

### Recommendation

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion must not silently downgrade `AllResources` to `L1Gas`. The protobuf schema should carry an explicit variant discriminator (e.g., a boolean `is_all_resources` flag or a separate oneof), or the deserialization should always produce `AllResources` when all three fields are present in the message, regardless of whether they are zero:

```rust
// Always produce AllResources when all three fields are present
Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))
```

The `L1Gas` variant should only be produced when the protobuf message explicitly signals a pre-0.13.3 transaction (e.g., when `l1_data_gas` is absent/`None` in the wire format, not merely zero).

---

### Proof of Concept

```
1. User submits RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds { l1_gas: 1000, l2_gas: 0, l1_data_gas: 0 }

2. Gateway converts to InternalRpcInvokeTransactionV3 (AllResources variant).
   Hash H_orig = poseidon(INVOKE, 3, sender, poseidon(tip, l1_concat, 0_l2_concat, 0_l1data_concat), ...)
                                                          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                                          3-element resource hash

3. Transaction included in block as InvokeTransactionV3 { resource_bounds: AllResources{...} }.

4. Block synced via P2P as TransactionInBlock protobuf:
     ResourceBounds { l1_gas: 1000, l2_gas: 0, l1_data_gas: 0 }

5. Receiving node deserializes:
     l1_data_gas.is_zero() && l2_gas.is_zero() → ValidResourceBounds::L1Gas(1000)

6. Receiving node recomputes hash:
   H_recv = poseidon(INVOKE, 3, sender, poseidon(tip, l1_concat, 0_l2_concat), ...)
                                                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                                  2-element resource hash (l1_data_gas MISSING)

7. H_orig ≠ H_recv → validate_transaction_hash returns false → block rejected.
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

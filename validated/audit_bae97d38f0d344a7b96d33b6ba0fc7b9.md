### Title
`AllResources`→`L1Gas` silent downgrade in protobuf deserialization produces a divergent transaction hash for zero-`l2_gas`/`l1_data_gas` transactions - (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf `ValidResourceBounds` deserializer uses a zero-value heuristic to decide between the `L1Gas` and `AllResources` variants. A valid `AllResources` transaction whose `l2_gas` and `l1_data_gas` fields are both zero is silently downgraded to `L1Gas` on the receiving side. Because `get_tip_resource_bounds_hash` produces structurally different hash preimages for the two variants (2 vs. 3 resource terms), the transaction hash computed by the receiving node diverges from the hash committed by the originating node, breaking hash verification in the P2P block-sync path.

---

### Finding Description

**Nullification heuristic in protobuf deserialization**

`crates/apollo_protobuf/src/converters/transaction.rs` lines 431–435:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

The heuristic is intended to distinguish legacy pre-0.13.3 `L1Gas` transactions (which never carry `l2_gas` or `l1_data_gas`) from modern `AllResources` transactions. However, a user can legitimately submit an `AllResources` transaction with only `l1_gas` non-zero and both `l2_gas` and `l1_data_gas` at their zero defaults. The gateway accepts such a transaction because `max_possible_fee(Tip::ZERO) > 0` when `l1_gas` is non-zero. [2](#0-1) 

**Hash domain divergence**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` produces different preimages for the two variants:

```
L1Gas      → poseidon(tip, L1_GAS_concat, L2_GAS_concat)           [2 resource terms]
AllResources → poseidon(tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat) [3 resource terms]
``` [3](#0-2) 

Even when `l1_data_gas` is zero, the `AllResources` branch appends `get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)` to the hash chain, producing a strictly longer preimage than the `L1Gas` branch. The two hashes are therefore always distinct.

**Originating node path**

`InternalRpcInvokeTransactionV3::resource_bounds()` unconditionally returns `ValidResourceBounds::AllResources(self.resource_bounds)`, so the hash stored in `InternalRpcTransaction.tx_hash` is always computed under the `AllResources` domain. [4](#0-3) 

**Receiving node path**

When the block is synced over P2P, the `InvokeTransactionV3` is serialized to protobuf (correctly as `AllResources` with `l1_data_gas = zero`) and then deserialized by the receiving node. The zero-check fires, the variant is downgraded to `L1Gas`, and any subsequent call to `get_invoke_transaction_v3_hash` or `validate_transaction_hash` computes hash H2 ≠ H1. [5](#0-4) 

---

### Impact Explanation

A receiving node that calls `validate_transaction_hash` on the deserialized transaction will compute H2 ≠ H1 and reject the block, stalling sync. Alternatively, if the hash is not re-verified, the wrong hash is persisted to the DB, causing incorrect receipts, wrong state, and divergent block hashes for any block containing such a transaction. This matches the "Transaction conversion or signature/hash logic binds the wrong … hash … or executable payload" (High) and "Wrong state, receipt … or revert result" (Critical) impact categories.

---

### Likelihood Explanation

The trigger requires an `AllResources` transaction with `l2_gas = {0, 0}` and `l1_data_gas = {0, 0}`. The gateway's `min_gas_price` check (`l2_gas.max_price_per_unit < min_gas_price`) blocks this when `min_gas_price > 0`, but the check is configuration-gated and can be zero. The `max_possible_fee` guard passes as long as `l1_gas` is non-zero. No other stateless or stateful check prevents admission. The condition is therefore reachable by any unprivileged user on a node with `min_gas_price = 0`.

---

### Recommendation

Remove the zero-value heuristic. The protobuf schema should carry an explicit discriminant (e.g., a `bool is_all_resources` field or a `oneof` wrapper) so the variant is preserved losslessly across serialization. Until the schema is updated, the serializer for `ValidResourceBounds::L1Gas` should write a sentinel non-zero value into `l1_data_gas` (e.g., `max_price_per_unit = 1`) to prevent the round-trip collision, and the deserializer should check that sentinel rather than a pure zero test.

---

### Proof of Concept

1. Submit `RpcInvokeTransactionV3` with `resource_bounds = AllResourceBounds { l1_gas: {max_amount:1000, max_price:1}, l2_gas: {0,0}, l1_data_gas: {0,0} }`.
2. Gateway accepts (max_possible_fee = 1000 > 0; `min_gas_price = 0` passes price check).
3. `InternalRpcInvokeTransactionV3::resource_bounds()` returns `AllResources` → hash H1 includes `L1_DATA_GAS` term.
4. Transaction is included in a block and committed.
5. Block is propagated to a peer via P2P sync; `InvokeTransactionV3` is serialized to protobuf with `l1_data_gas = {0,0}`.
6. Peer deserializes: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas(l1_gas)`.
7. Peer calls `get_invoke_transaction_v3_hash` → hash H2 omits `L1_DATA_GAS` term → H2 ≠ H1.
8. `validate_transaction_hash` returns `false`; peer rejects the block or stores the wrong hash. [6](#0-5) [7](#0-6)

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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-88)
```rust
    fn validate_resource_bounds(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if !self.config.validate_resource_bounds {
            return Ok(());
        }

        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }

        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
        }

        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }

        Ok(())
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

**File:** crates/starknet_api/src/transaction_hash.rs (L188-210)
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

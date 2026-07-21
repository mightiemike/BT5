### Title
`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` Misclassifies `AllResources` Transactions as `L1Gas`, Binding Wrong Hash Preimage During P2P Conversion — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf-to-domain conversion for `ValidResourceBounds` uses a value-based heuristic (`l2_gas.is_zero() && l1_data_gas.is_zero()`) to decide whether a received transaction carries `L1Gas` (pre-0.13.3) or `AllResources` (post-0.13.3) resource bounds. A V3 transaction that was originally signed and hashed as `AllResources` with zero `l2_gas` and zero `l1_data_gas` is silently reclassified as `L1Gas` on the receiving side. Because `get_tip_resource_bounds_hash` produces a structurally different hash preimage for the two variants (two vs. three resource elements), the hash recomputed from the misclassified type diverges from the signer's hash, breaking the hash/signature binding invariant.

### Finding Description

In `crates/apollo_protobuf/src/converters/transaction.rs`, the `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` implementation reads:

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

The classification is purely value-based: any transaction whose `l2_gas` and `l1_data_gas` fields are both zero is mapped to `ValidResourceBounds::L1Gas`, regardless of whether the sender originally signed it as `AllResources`.

A V3 transaction submitted through the gateway carries `AllResourceBounds` (the only type accepted by `RpcInvokeTransactionV3` and `InternalRpcInvokeTransactionV3`): [2](#0-1) 

When converted to the executable `InvokeTransactionV3`, the resource bounds are always wrapped as `ValidResourceBounds::AllResources`: [3](#0-2) 

The transaction hash is then computed by `get_invoke_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash`. For `AllResources`, this function appends a third element — `concat(L1_DATA_GAS, 0, 0)` — to the Poseidon hash chain:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
``` [4](#0-3) 

So the signed hash `H_AllResources = poseidon(tip, L1_GAS_elem, L2_GAS_elem(0,0), L1_DATA_GAS_elem(0,0))`.

When this block is later received over P2P and the transaction is deserialized via `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, the receiving node classifies it as `ValidResourceBounds::L1Gas`. The hash recomputed from this misclassified type is:

`H_L1Gas = poseidon(tip, L1_GAS_elem, L2_GAS_elem(0,0))`

`H_AllResources ≠ H_L1Gas` because the third element is absent. Any subsequent call to `validate_transaction_hash` with the misclassified `InvokeTransactionV3` will compute `H_L1Gas` and fail to match the stored/expected `H_AllResources`. [5](#0-4) 

The parallel converter used for the RPC/consensus mempool path (`TryFrom<protobuf::ResourceBounds> for AllResourceBounds` in `rpc_transaction.rs`) does **not** have this ambiguity — it always produces `AllResourceBounds` and requires all three fields: [6](#0-5) 

The divergence between the two converters is the root cause.

### Impact Explanation

The P2P block-sync path converts received transactions using `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. If a block contains a V3 transaction with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`, the receiving node misclassifies it as `L1Gas(X)`. When the node recomputes the transaction hash to validate the block's transaction commitment, it produces `H_L1Gas ≠ H_AllResources`. This causes the block to fail hash validation and be rejected, even though it is a legitimately produced block. This matches the impact: **"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload"** (High).

### Likelihood Explanation

A transaction with `AllResources { l1_gas > 0, l2_gas = 0, l1_data_gas = 0 }` passes the gateway's stateless resource-bounds check (non-zero `max_possible_fee`) when `min_gas_price = 0` or when `validate_resource_bounds = false`. The gateway config exposes both flags: [7](#0-6) 

Any node operator running with `min_gas_price = 0` (the default in testing configurations) can produce such transactions. Peers syncing from that node will misclassify them and fail block validation.

### Recommendation

Replace the value-based heuristic with an explicit version/type tag. The protobuf `ResourceBounds` message should carry a discriminant field (e.g., a boolean `is_all_resources`) set by the sender, so the receiver can reconstruct the exact variant without inspecting field values. Until then, the `unwrap_or_default` for `l1_data_gas` should be changed to an error when the peer claims to be a post-0.13.2 node, matching the intent of the existing TODO comment: [8](#0-7) 

Alternatively, always deserialize into `AllResources` when the peer is known to be post-0.13.3, and only fall back to `L1Gas` for explicitly versioned legacy messages.

### Proof of Concept

1. Submit a V3 invoke transaction with `resource_bounds = AllResourceBounds { l1_gas: { max_amount: 1000, max_price_per_unit: 1 }, l2_gas: { 0, 0 }, l1_data_gas: { 0, 0 } }` through the gateway (passes stateless validation when `min_gas_price = 0`).
2. The gateway computes `tx_hash = H_AllResources` (includes `L1_DATA_GAS_elem(0,0)` in Poseidon chain).
3. The transaction is included in a block and propagated over P2P.
4. A peer receives the block and deserializes the transaction's `ResourceBounds` via `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`: since `l2_gas.is_zero() && l1_data_gas.is_zero()`, it produces `ValidResourceBounds::L1Gas(l1_gas)`.
5. The peer calls `validate_transaction_hash` with the misclassified `InvokeTransactionV3`; `get_tip_resource_bounds_hash` omits the `L1_DATA_GAS` element, computing `H_L1Gas ≠ H_AllResources`.
6. Hash validation fails; the peer rejects the block as invalid, even though it was correctly produced.

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L615-628)
```rust
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, SizeOf)]
pub struct InternalRpcInvokeTransactionV3 {
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
    pub proof_facts: ProofFacts,
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L679-694)
```rust
impl From<InternalRpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: InternalRpcInvokeTransactionV3) -> Self {
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

**File:** crates/starknet_api/src/transaction_hash.rs (L202-208)
```rust
    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });
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

### Title
Protobuf round-trip silently downgrades `AllResources` to `L1Gas` when L2/data-gas are zero, producing a divergent transaction hash — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary
The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion classifies any incoming protobuf message as `ValidResourceBounds::L1Gas` whenever both `l2_gas` and `l1_data_gas` are zero, regardless of whether the transaction was originally signed and hashed as `AllResources`. Because `get_tip_resource_bounds_hash` feeds a different number of elements into the Poseidon hash for `L1Gas` (3 elements) versus `AllResources` with zero L2/data gas (4 elements), any transaction submitted via the RPC gateway with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` will have its hash silently changed after a protobuf round-trip. This causes hash validation failures on every node that receives the transaction or its containing block via P2P sync.

### Finding Description

**Step 1 – Gateway accepts `AllResources` with zero L2/data gas.**

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds`. The gateway's `validate_resource_bounds` only checks that `max_possible_fee > 0`; a transaction with `l1_gas = X > 0, l2_gas = 0, l1_data_gas = 0` passes cleanly. [1](#0-0) 

**Step 2 – Hash is computed with the `AllResources` path (4 elements).**

`InternalRpcInvokeTransactionV3::resource_bounds` is `AllResourceBounds`. Its `InvokeTransactionV3Trait` implementation wraps it in `ValidResourceBounds::AllResources(...)`. `get_tip_resource_bounds_hash` then hashes `[tip, pack(L1_GAS, X), pack(L2_GAS, 0), pack(L1_DATA_GAS, 0)]` — **4 elements**. [2](#0-1) [3](#0-2) 

**Step 3 – Serialisation to protobuf preserves the zero fields.**

`From<ValidResourceBounds> for protobuf::ResourceBounds` serialises `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` as `{ l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` — all three fields present, both L2 fields zero. [4](#0-3) 

**Step 4 – Deserialisation silently downgrades to `L1Gas`.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` checks `l1_data_gas.is_zero() && l2_gas.is_zero()` and returns `ValidResourceBounds::L1Gas(l1_gas)`. The `AllResources` variant is lost. [5](#0-4) 

**Step 5 – Hash is recomputed with the `L1Gas` path (3 elements).**

After deserialisation the receiving node calls `get_tip_resource_bounds_hash` with `L1Gas(X)`, which hashes `[tip, pack(L1_GAS, X), pack(L2_GAS, 0)]` — **3 elements**. The Poseidon hash of 3 elements ≠ Poseidon hash of 4 elements, so the recomputed hash H₂ ≠ original hash H₁. [6](#0-5) 

**Step 6 – Hash validation fails.**

`validate_transaction_hash` compares the recomputed hash against the expected hash stored in the block. The mismatch causes the block to be rejected. [7](#0-6) 

The `starknet_api::transaction::InvokeTransactionV3` (used in the storage/sync layer) carries `resource_bounds: ValidResourceBounds`, so the downgrade propagates into every code path that reconstructs the hash from stored transaction data. [8](#0-7) 

### Impact Explanation

A sequencer node that accepts and sequences a transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` will commit a block whose transaction hash H₁ cannot be reproduced by any peer that receives the block via P2P protobuf sync. Every such peer recomputes H₂ ≠ H₁ and rejects the block, causing a permanent state-sync split between the sequencer and all follower nodes. This matches the **Critical** impact category: "Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input," and the **High** impact category: "Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."

### Likelihood Explanation

Any user who submits an invoke (or deploy-account) transaction with only L1 gas bounds set and L2/data gas left at zero triggers the condition. This is a natural usage pattern for pre-0.13.3 style transactions submitted through the new gateway. No special privileges are required; the gateway explicitly accepts such transactions.

### Recommendation

1. **Canonical serialisation gate**: In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, remove the `L1Gas` downgrade path entirely for transactions received from peers running protocol ≥ 0.13.3. All such transactions must be `AllResources`.
2. **Alternatively, enforce a canonical form at ingestion**: In `From<ValidResourceBounds> for protobuf::ResourceBounds`, tag the wire message with the original variant so the receiver can reconstruct the exact same type without heuristic inference.
3. **Hash-domain guard**: `get_tip_resource_bounds_hash` should assert that `AllResources` with all-zero L2/data gas is never produced from a transaction that was originally signed as `L1Gas`, or vice versa, by tying the variant choice to the transaction version field rather than to the zero-ness of the bounds.

### Proof of Concept

```
1. User submits RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds { l1_gas: {max_amount: 1000, max_price: 1}, l2_gas: {0,0}, l1_data_gas: {0,0} }

2. Gateway computes H1 = poseidon([tip, pack(L1_GAS,1000,1), pack(L2_GAS,0,0), pack(L1_DATA_GAS,0,0)])
   (4-element hash, AllResources path)

3. Transaction is included in block B with stored hash H1.

4. Block B is serialised to protobuf for P2P sync:
     ResourceBounds { l1_gas: {1000,1}, l2_gas: {0,0}, l1_data_gas: {0,0} }

5. Peer deserialises:
     l1_data_gas.is_zero() && l2_gas.is_zero()  →  ValidResourceBounds::L1Gas({1000,1})

6. Peer recomputes H2 = poseidon([tip, pack(L1_GAS,1000,1), pack(L2_GAS,0,0)])
   (3-element hash, L1Gas path)

7. H1 ≠ H2  →  validate_transaction_hash returns false  →  block B rejected by peer.
```

### Citations

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
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

**File:** crates/starknet_api/src/transaction.rs (L310-346)
```rust
impl TransactionHasher for DeclareTransactionV2 {
    fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
        transaction_version: &TransactionVersion,
    ) -> Result<TransactionHash, StarknetApiError> {
        get_declare_transaction_v2_hash(self, chain_id, transaction_version)
    }
}

/// A declare V3 transaction.
#[cfg_attr(any(test, feature = "testing"), derive(Default))]
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
pub struct DeclareTransactionV3 {
    pub resource_bounds: ValidResourceBounds,
    pub tip: Tip,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub class_hash: ClassHash,
    pub compiled_class_hash: CompiledClassHash,
    pub sender_address: ContractAddress,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
}

impl TransactionHasher for DeclareTransactionV3 {
    fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
        transaction_version: &TransactionVersion,
    ) -> Result<TransactionHash, StarknetApiError> {
        get_declare_transaction_v3_hash(self, chain_id, transaction_version)
    }
}

```

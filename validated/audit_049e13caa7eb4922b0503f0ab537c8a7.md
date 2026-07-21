### Title
Non-Canonical `ValidResourceBounds` Classification in Protobuf Converter Produces Divergent Transaction Hash for V3 Transactions with Zero L2/Data Gas Bounds — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf converter for `ResourceBounds` classifies a V3 transaction as `ValidResourceBounds::L1Gas` whenever both `l2_gas` and `l1_data_gas` are zero. The RPC submission path, by contrast, always produces `ValidResourceBounds::AllResources` for the same field values. Because `get_tip_resource_bounds_hash` hashes a different number of elements for each variant, the same transaction data produces two distinct Poseidon hashes depending on which ingestion path is used. This is the sequencer-native analog of the external report's "normalization present in one code path but absent in another" pattern.

---

### Finding Description

**Root cause — protobuf converter (`crates/apollo_protobuf/src/converters/transaction.rs`, lines 417–436):**

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← 3-element hash preimage
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})                                               // ← 4-element hash preimage
``` [1](#0-0) 

**RPC submission path — always `AllResources`:**

`InternalRpcInvokeTransactionV3` stores `resource_bounds: AllResourceBounds` (never `L1Gas`). Its `InvokeTransactionV3Trait` implementation wraps it unconditionally as `ValidResourceBounds::AllResources`. [2](#0-1) 

**Hash divergence — `get_tip_resource_bounds_hash` (`crates/starknet_api/src/transaction_hash.rs`, lines 188–211):**

```rust
// For L1Gas:  hashes [tip, L1_GAS_packed, L2_GAS_packed(zero)]          → 3 elements
// For AllResources (zero L2/data): hashes [tip, L1_GAS_packed, L2_GAS_packed(zero), L1_DATA_GAS_packed(zero)] → 4 elements
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],          // L1_DATA_GAS omitted
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
``` [3](#0-2) 

The two Poseidon hashes over inputs of different lengths are cryptographically distinct. The exact divergent values are:

- **H₁** = `Poseidon([tip, pack(L1_GAS), pack(L2_GAS=0), pack(L1_DATA=0)])` — computed on the RPC/gateway path
- **H₂** = `Poseidon([tip, pack(L1_GAS), pack(L2_GAS=0)])` — computed on the protobuf/state-sync path

H₁ ≠ H₂ for any non-trivial `L1_GAS` value.

**Trigger condition:**

A V3 transaction with non-zero `l1_gas` but `l2_gas = {max_amount:0, max_price:0}` and `l1_data_gas = {max_amount:0, max_price:0}` passes the gateway's zero-resource-bounds check (because `l1_gas` is non-zero) and is accepted. When this transaction is later received by a peer via the protobuf state-sync path, the converter classifies it as `L1Gas` and recomputes H₂ ≠ H₁. [4](#0-3) 

---

### Impact Explanation

1. **Wrong hash served by RPC** — A syncing node that recomputes the transaction hash from the protobuf-deserialized `InvokeTransactionV3` (e.g., via `validate_transaction_hash` or `get_transaction_hash`) obtains H₂ while the block records H₁. Any RPC call that re-derives the hash from stored transaction fields (`starknet_getTransactionByHash`, trace, simulation) returns an authoritative-looking but wrong value.

2. **Transaction conversion binds wrong hash** — The `TryFrom<(Transaction, &ChainId)> for executable_transaction::Transaction` path recomputes the hash from the `InvokeTransactionV3` struct. If that struct was deserialized from protobuf with `L1Gas` classification, the executable transaction carries H₂ instead of H₁, binding the wrong hash to the executable payload. [5](#0-4) 

3. **Block rejection on sync** — If a syncing node verifies that the recomputed hash matches the hash in the block header, it will reject a valid block containing such a transaction, causing a liveness failure.

---

### Likelihood Explanation

- **Unprivileged trigger**: Any user can craft a V3 transaction with zero `l2_gas` and `l1_data_gas`. The gateway's `validate_resource_bounds` check only rejects transactions where *all* resource bounds are zero; a transaction with non-zero `l1_gas` passes.
- **Gating**: If `min_gas_price > 0`, the check `resource_bounds.l2_gas.max_price_per_unit < self.config.min_gas_price` would reject zero-L2-price transactions. However, `validate_resource_bounds` is a configurable flag that can be disabled, and the bootstrap phase explicitly disables it.
- **Protobuf path is production code**: State sync between sequencer nodes uses the protobuf `InvokeV3` converter.

---

### Recommendation

Remove the `L1Gas`/`AllResources` branching from the protobuf converter. For V3 transactions, always produce `ValidResourceBounds::AllResources`, regardless of whether `l2_gas` and `l1_data_gas` are zero. The `L1Gas` variant should only be produced when the transaction version is explicitly pre-0.13.3 (e.g., V1):

```rust
// In TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
// Always use AllResources for V3; only use L1Gas for V1/V0 transactions.
Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))
```

Alternatively, add a version field to the protobuf converter context so the classification is driven by the transaction version rather than by zero-ness of fields.

The same fix should be applied to `From<ResourceBoundsMapping> for ValidResourceBounds` in `crates/apollo_rpc/src/v0_8/transaction.rs` (lines 188–199), which has the identical branching logic. [6](#0-5) 

---

### Proof of Concept

Given a V3 invoke transaction with:
- `l1_gas = { max_amount: 1000, max_price_per_unit: 1 }`
- `l2_gas = { max_amount: 0, max_price_per_unit: 0 }`
- `l1_data_gas = { max_amount: 0, max_price_per_unit: 0 }`

**Path A (RPC → `InternalRpcInvokeTransactionV3`):**
`resource_bounds()` returns `ValidResourceBounds::AllResources(...)`.
`get_tip_resource_bounds_hash` hashes 4 elements: `[tip, pack(L1_GAS,1000,1), pack(L2_GAS,0,0), pack(L1_DATA,0,0)]`.

**Path B (protobuf `InvokeV3` → `InvokeTransactionV3`):**
`l2_gas.is_zero() && l1_data_gas.is_zero()` → `ValidResourceBounds::L1Gas(l1_gas)`.
`get_tip_resource_bounds_hash` hashes 3 elements: `[tip, pack(L1_GAS,1000,1), pack(L2_GAS,0,0)]`.

The Poseidon hash over 4 elements ≠ Poseidon hash over 3 elements. The transaction hash H₁ (Path A) ≠ H₂ (Path B). A node that accepted the transaction via Path A and later receives it via Path B (state sync) will compute a mismatched hash. [7](#0-6) [8](#0-7)

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
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

**File:** crates/starknet_api/src/transaction.rs (L154-185)
```rust
impl TryFrom<(Transaction, &ChainId)> for executable_transaction::Transaction {
    type Error = StarknetApiError;

    fn try_from((tx, chain_id): (Transaction, &ChainId)) -> Result<Self, Self::Error> {
        let tx_hash = tx.calculate_transaction_hash(chain_id)?;
        match tx {
            Transaction::DeployAccount(tx) => {
                let contract_address = tx.calculate_contract_address()?;
                Ok(executable_transaction::Transaction::Account(
                    executable_transaction::AccountTransaction::DeployAccount(
                        executable_transaction::DeployAccountTransaction {
                            tx,
                            tx_hash,
                            contract_address,
                        },
                    ),
                ))
            }
            Transaction::Invoke(tx) => Ok(executable_transaction::Transaction::Account(
                executable_transaction::AccountTransaction::Invoke(
                    executable_transaction::InvokeTransaction { tx, tx_hash },
                ),
            )),
            _ => {
                unimplemented!(
                    "Unsupported transaction type. Only DeployAccount and Invoke are currently \
                     supported. tx: {:?}",
                    tx
                )
            }
        }
    }
```

**File:** crates/apollo_rpc/src/v0_8/transaction.rs (L188-199)
```rust
impl From<ResourceBoundsMapping> for ValidResourceBounds {
    fn from(value: ResourceBoundsMapping) -> Self {
        if value.l1_data_gas.is_zero() && value.l2_gas.is_zero() {
            Self::L1Gas(value.l1_gas)
        } else {
            Self::AllResources(AllResourceBounds {
                l1_gas: value.l1_gas,
                l1_data_gas: value.l1_data_gas,
                l2_gas: value.l2_gas,
            })
        }
    }
```

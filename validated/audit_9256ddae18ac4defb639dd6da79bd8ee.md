### Title
`ValidResourceBounds` Protobuf Deserializer Silently Downgrades `AllResources` to `L1Gas`, Producing Wrong Transaction Hash and Execution Mode on Syncing Nodes — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` applies a value-based heuristic that silently converts `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` into `L1Gas(X)` after a P2P round-trip. Because the transaction hash function and the fee/gas computation mode both branch on this enum variant, a syncing node that receives such a transaction via P2P state sync will compute a different hash and execute the transaction under a different resource-accounting mode than the proposing node did.

---

### Finding Description

**Root cause — the lossy protobuf converter**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` (lines 417–436) applies the following heuristic on deserialization:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

The serializer (`From<ValidResourceBounds> for protobuf::ResourceBounds`, lines 471–489) always emits all three fields, including a zero `l1_data_gas` for `AllResources`: [2](#0-1) 

So the round-trip `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` → protobuf → `L1Gas(X)` is lossy: the variant changes even though the numeric values are identical.

**Why the variant matters for hashing**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` branches on the variant:

- `L1Gas` → hashes only `[tip, L1_GAS_concat, L2_GAS_concat]` (2 resource elements)
- `AllResources` → hashes `[tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat]` (3 resource elements, even when `l1_data_gas` is zero) [3](#0-2) 

A transaction submitted with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` gets hash H₁ (3-element poseidon chain). After the protobuf round-trip on a syncing peer it is deserialized as `L1Gas(X)` and produces hash H₂ (2-element poseidon chain). H₁ ≠ H₂.

**Why the variant matters for execution**

`get_gas_vector_computation_mode()` returns `GasVectorComputationMode::All` for `AllResources` and `GasVectorComputationMode::NoL2Gas` for `L1Gas`: [4](#0-3) 

This mode controls which resources are metered during pre-validation and fee checking in the blockifier: [5](#0-4) 

A syncing node that re-executes the transaction (e.g., for proof generation) will use `NoL2Gas` mode instead of `All` mode, producing different gas usage, different receipts, and different state diffs.

**The trigger is a valid, gateway-accepted transaction**

The gateway's stateless validator explicitly accepts a V3 transaction with only `l1_gas` non-zero: [6](#0-5) 

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds` (not `ValidResourceBounds`), so the gateway and consensus paths use the separate `TryFrom<protobuf::ResourceBounds> for AllResourceBounds` converter (lines 212–223 of `rpc_transaction.rs`) which is lossless. The lossy converter is only hit in the P2P state-sync path where transactions are transmitted as `InvokeTransactionV3` (the storage/blockifier type that carries `ValidResourceBounds`). [7](#0-6) 

---

### Impact Explanation

A syncing node that receives a block containing a V3 transaction with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` via P2P state sync will:

1. Store the transaction with the wrong `ValidResourceBounds` variant (`L1Gas` instead of `AllResources`).
2. Compute a different transaction hash than the proposing node (H₂ ≠ H₁), breaking hash-based lookups and receipt verification.
3. Re-execute the transaction under `NoL2Gas` mode instead of `All` mode, producing wrong gas accounting, wrong receipts, and wrong state diffs.
4. Generate an incorrect STARK proof (if the node is a prover), since the OS re-executes from the stored transaction data.

This matches: **Wrong state, receipt, or revert result from blockifier/execution logic for accepted input** and **Transaction conversion or signature/hash logic binds the wrong hash or executable payload**.

---

### Likelihood Explanation

Any user can submit a V3 invoke transaction with only `l1_gas` non-zero and `l2_gas = l1_data_gas = 0`. This passes all gateway validation. The divergence is triggered automatically on every syncing node that processes such a block. No privileged access is required.

---

### Recommendation

Remove the value-based heuristic from `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. Instead, use a dedicated discriminant field in the protobuf message to distinguish `L1Gas` from `AllResources`, or always deserialize as `AllResources` when all three fields are present (even if zero). The `AllResourceBounds` converter in `rpc_transaction.rs` (lines 212–223) is the correct model: it always returns `AllResourceBounds` regardless of zero values.

Concretely, change:

```rust
// BEFORE (lossy)
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

to a scheme that preserves the original variant, e.g., by adding a boolean `is_l1_gas_only` field to `protobuf::ResourceBounds`, or by always emitting `AllResources` for V3 transactions and only using `L1Gas` for explicitly-versioned pre-0.13.3 transactions.

---

### Proof of Concept

```
1. Craft a valid V3 invoke transaction with:
     resource_bounds = AllResourceBounds {
         l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
     }

2. Submit to gateway → accepted. Gateway computes hash H₁ using AllResources
   (3-element poseidon chain including L1_DATA_GAS=0).

3. Transaction is included in a block. Proposer stores InvokeTransactionV3 with
   ValidResourceBounds::AllResources.

4. Syncing peer receives the block via P2P state sync. The InvokeTransactionV3 is
   serialized to protobuf::ResourceBounds with l1_data_gas = Some(zero).

5. Syncing peer deserializes: l1_data_gas.is_zero() && l2_gas.is_zero() → true →
   returns ValidResourceBounds::L1Gas(l1_gas).

6. Syncing peer computes hash H₂ using L1Gas (2-element poseidon chain, no
   L1_DATA_GAS). H₁ ≠ H₂.

7. Syncing peer re-executes the transaction under GasVectorComputationMode::NoL2Gas
   instead of ::All, producing different fee accounting, different receipt, and
   different state diff than the proposing node.
``` [8](#0-7) [3](#0-2) [9](#0-8)

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L471-489)
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

**File:** crates/starknet_api/src/transaction/fields.rs (L363-421)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}

impl From<AllResourceBounds> for ValidResourceBounds {
    fn from(value: AllResourceBounds) -> Self {
        Self::AllResources(value)
    }
}

impl ValidResourceBounds {
    pub fn get_l1_bounds(&self) -> ResourceBounds {
        match self {
            Self::L1Gas(l1_bounds) => *l1_bounds,
            Self::AllResources(AllResourceBounds { l1_gas, .. }) => *l1_gas,
        }
    }

    pub fn get_l2_bounds(&self) -> ResourceBounds {
        match self {
            Self::L1Gas(_) => ResourceBounds::default(),
            Self::AllResources(AllResourceBounds { l2_gas, .. }) => *l2_gas,
        }
    }

    /// Returns the maximum possible fee that can be charged for the transaction.
    /// The computation is saturating, meaning that if the result is larger than the maximum
    /// possible fee, the maximum possible fee is returned.
    pub fn max_possible_fee(&self, tip: Tip) -> Fee {
        match self {
            ValidResourceBounds::L1Gas(l1_bounds) => {
                l1_bounds.max_amount.saturating_mul(l1_bounds.max_price_per_unit)
            }
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
    }

    pub fn get_gas_vector_computation_mode(&self) -> GasVectorComputationMode {
        match self {
            Self::AllResources(_) => GasVectorComputationMode::All,
            Self::L1Gas(_) => GasVectorComputationMode::NoL2Gas,
        }
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L386-426)
```rust
        match tx_info {
            TransactionInfo::Current(context) => {
                let resources_amount_tuple = match &context.resource_bounds {
                    ValidResourceBounds::L1Gas(l1_gas_resource_bounds) => vec![(
                        L1Gas,
                        l1_gas_resource_bounds,
                        minimal_gas_amount_vector.to_l1_gas_for_fee(
                            tx_context.get_gas_prices(),
                            &tx_context.block_context.versioned_constants,
                        ),
                        block_info.gas_prices.l1_gas_price(fee_type),
                    )],
                    ValidResourceBounds::AllResources(AllResourceBounds {
                        l1_gas: l1_gas_resource_bounds,
                        l2_gas: l2_gas_resource_bounds,
                        l1_data_gas: l1_data_gas_resource_bounds,
                    }) => {
                        let GasPriceVector { l1_gas_price, l1_data_gas_price, l2_gas_price } =
                            block_info.gas_prices.gas_price_vector(fee_type);
                        vec![
                            (
                                L1Gas,
                                l1_gas_resource_bounds,
                                minimal_gas_amount_vector.l1_gas,
                                *l1_gas_price,
                            ),
                            (
                                L1DataGas,
                                l1_data_gas_resource_bounds,
                                minimal_gas_amount_vector.l1_data_gas,
                                *l1_data_gas_price,
                            ),
                            (
                                L2Gas,
                                l2_gas_resource_bounds,
                                minimal_gas_amount_vector.l2_gas,
                                *l2_gas_price,
                            ),
                        ]
                    }
                };
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

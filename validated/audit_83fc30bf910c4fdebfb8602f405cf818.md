### Title
Protobuf `ValidResourceBounds` Deserialization Uses Zero-Value Proxy to Select Transaction Variant, Misclassifying `AllResources` Transactions as `L1Gas` - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf-to-`ValidResourceBounds` conversion in `crates/apollo_protobuf/src/converters/transaction.rs` uses the derived condition `l1_data_gas.is_zero() && l2_gas.is_zero()` as a proxy to decide whether a deserialized transaction is `ValidResourceBounds::L1Gas` (pre-0.13.3) or `ValidResourceBounds::AllResources` (post-0.13.3). A legitimate `AllResources` V3 transaction with zero `l2_gas` and `l1_data_gas` bounds is silently reclassified as `L1Gas` after P2P deserialization. This binds the wrong transaction type to the executable payload, producing a different transaction hash preimage and a different fee/resource-accounting path than the one the sender signed.

---

### Finding Description

In `crates/apollo_protobuf/src/converters/transaction.rs` lines 417–437, the `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` implementation selects the internal variant by inspecting the *values* of the deserialized fields rather than an explicit type tag:

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
let l1_gas: ResourceBounds = l1_gas.try_into()?;
let l2_gas: ResourceBounds = l2_gas.try_into()?;
let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← chosen when both are zero
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

The intent is to distinguish old pre-0.13.3 `L1Gas`-only transactions (which never carry `l2_gas` or `l1_data_gas`) from new `AllResources` transactions. However, a post-0.13.3 `AllResources` transaction is permitted to set both `l2_gas` and `l1_data_gas` to zero — for example, a user who only wants to pay for L1 gas. The gateway validator explicitly accepts such transactions (test case `valid_l1_gas` in `stateless_transaction_validator_test.rs` passes with only `l1_gas` non-zero). All V3 transactions submitted via the RPC API use `AllResourceBounds` (i.e., `AllResources` variant), so this scenario is reachable without any special privilege.

When such a transaction is serialized to protobuf by the originating node and deserialized by a peer, the peer reconstructs it as `ValidResourceBounds::L1Gas` instead of `ValidResourceBounds::AllResources`.

The `get_tip_resource_bounds_hash` function in `crates/starknet_api/src/transaction_hash.rs` lines 188–210 produces **different hash preimages** for the two variants:

- `L1Gas` path: hashes `[tip, pack(L1_GAS, …), pack(L2_GAS, 0)]`
- `AllResources` path: hashes `[tip, pack(L1_GAS, …), pack(L2_GAS, 0), pack(L1_DATA_GAS, 0)]`

The extra `pack(L1_DATA_GAS, 0)` element makes the Poseidon hash differ even when the numeric values are identical. The sender signed the `AllResources` hash; the receiving node reconstructs the `L1Gas` hash. These are structurally distinct values.

Additionally, `get_gas_vector_computation_mode()` returns `NoL2Gas` for `L1Gas` and `All` for `AllResources`, routing the transaction through a different fee-check branch in `check_resources_within_bounds`.

---

### Impact Explanation

**Transaction hash mismatch (High — wrong hash bound to executable payload):** Any node that recomputes the transaction hash from the deserialized fields (e.g., during mempool admission or `validate_transaction_hash`) will derive the `L1Gas` hash, which differs from the `AllResources` hash the sender signed. This causes a valid transaction to be rejected as having an invalid hash.

**Wrong fee/resource accounting (Critical — incorrect resource accounting with economic impact):** Even if the hash is not recomputed, the `L1Gas` variant routes execution through `GasVectorComputationMode::NoL2Gas`, which converts all gas to an L1-equivalent before checking bounds, rather than checking each resource independently. This diverges from the fee model the sender agreed to and that the sequencer should enforce.

---

### Likelihood Explanation

The trigger requires only a standard V3 `AllResources` transaction with `l2_gas = 0` and `l1_data_gas = 0`. The gateway explicitly accepts such transactions. Any transaction propagated over the P2P consensus layer passes through this protobuf conversion on every receiving peer. No attacker capability beyond submitting a normal transaction is required.

---

### Recommendation

Replace the value-based proxy check with an explicit type discriminator. The protobuf `ResourceBounds` message should carry a boolean or enum field (e.g., `is_all_resources`) that is set by the serializer and read by the deserializer, so the variant is preserved across the wire boundary regardless of whether the numeric values happen to be zero.

As an immediate fix, always deserialize as `AllResources` when all three fields (`l1_gas`, `l2_gas`, `l1_data_gas`) are present in the protobuf message, and only fall back to `L1Gas` when `l2_gas` is absent (i.e., the field is `None` before `unwrap_or_default`):

```rust
Ok(if value.l2_gas.is_none() && value.l1_data_gas.is_none() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

---

### Proof of Concept

1. Submit a V3 `AllResources` invoke transaction with `l1_gas = {max_amount: 1000, max_price_per_unit: 1}`, `l2_gas = {0, 0}`, `l1_data_gas = {0, 0}`. The gateway accepts it (matches `valid_l1_gas` test case). The transaction hash is computed via `get_tip_resource_bounds_hash` with the `AllResources` branch, including `pack(L1_DATA_GAS, 0)` in the Poseidon input.

2. The originating node serializes the transaction to `protobuf::ResourceBounds` via `From<ValidResourceBounds>` (lines 471–489): all three fields are `Some(…)`, with `l2_gas` and `l1_data_gas` encoding zero.

3. A peer receives the protobuf message and calls `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` (lines 417–437). `l1_data_gas.is_zero() && l2_gas.is_zero()` evaluates to `true`, so the peer reconstructs `ValidResourceBounds::L1Gas(l1_gas)`.

4. The peer calls `get_tip_resource_bounds_hash` with the `L1Gas` branch, which omits `pack(L1_DATA_GAS, 0)`. The resulting Poseidon hash differs from the sender's hash.

5. Any hash-validation step on the peer (e.g., `validate_transaction_hash`) compares the recomputed `L1Gas` hash against the stored `AllResources` hash and rejects the transaction as invalid — a valid transaction is dropped before sequencing. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L417-437)
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

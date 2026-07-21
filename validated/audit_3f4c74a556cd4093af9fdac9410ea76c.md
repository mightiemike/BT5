### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Wrong Transaction Hash — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf converter `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` uses a value-based heuristic — checking whether `l2_gas` and `l1_data_gas` are both zero — to decide which `ValidResourceBounds` variant to produce. A valid V3 transaction that explicitly carries `AllResources` bounds with `l2_gas = 0` and `l1_data_gas = 0` is silently re-classified as `L1Gas` after a protobuf round-trip. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` field in the hash only for `AllResources`, the hash computed from the deserialized transaction diverges from the hash computed at submission time, breaking hash validation for any node that receives the transaction over P2P.

---

### Finding Description

**Hash divergence in `get_tip_resource_bounds_hash`**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` builds the resource-bounds hash differently depending on the `ValidResourceBounds` variant:

```
L1Gas      → [tip, L1_GAS_field, L2_GAS_field]          (2 resource felts)
AllResources → [tip, L1_GAS_field, L2_GAS_field, L1_DATA_GAS_field]  (3 resource felts)
```

Even when `l2_gas = 0` and `l1_data_gas = 0`, the `AllResources` path appends `get_concat_resource(&zero, L1_DATA_GAS)` — a non-zero felt encoding the `L1_DATA` resource name — so the two Poseidon hashes are distinct. [1](#0-0) 

**Protobuf converter erases the variant**

The converter decides the variant purely from the numeric values of the deserialized fields:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

A V3 transaction submitted with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is serialised to protobuf with all three fields present. On deserialisation the converter sees `l2_gas.is_zero() && l1_data_gas.is_zero()` and returns `L1Gas(X)`, discarding the `AllResources` identity. [2](#0-1) 

**The `L1Gas` variant is semantically distinct from `AllResources` with zero bounds**

`ValidResourceBounds::L1Gas` is documented as the pre-0.13.3 representation. It also changes `get_gas_vector_computation_mode` from `All` to `NoL2Gas`, altering fee-validation logic downstream. [3](#0-2) 

**Reachable submission path**

The gateway's stateless validator explicitly accepts transactions with only `l1_gas` non-zero (test case `valid_l1_gas`), so a user can trivially submit a V3 `AllResources` transaction with `l2_gas = 0` and `l1_data_gas = 0`. [4](#0-3) 

**P2P sync uses `InvokeTransactionV3` with `ValidResourceBounds`**

The P2P block-sync path deserialises transactions through `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3`, which calls `ValidResourceBounds::try_from(...)` — the same buggy converter. The resulting `InvokeTransactionV3` carries `L1Gas` instead of `AllResources`, so any subsequent call to `calculate_transaction_hash` / `validate_transaction_hash` produces the wrong hash. [5](#0-4) 

---

### Impact Explanation

This is a **High** impact finding matching: *"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."*

A syncing node that receives a block containing a V3 `AllResources` transaction with `l2_gas = 0` and `l1_data_gas = 0` will:
1. Deserialise the transaction as `L1Gas`.
2. Recompute the hash — which omits the `L1_DATA_GAS` felt — obtaining a value that differs from the sequencer's canonical hash.
3. Fail hash validation and reject the block, stalling sync.

Additionally, if the wrong variant reaches the blockifier, `get_gas_vector_computation_mode` returns `NoL2Gas` instead of `All`, silently altering fee-bound enforcement for the transaction.

---

### Likelihood Explanation

Any user can craft a valid V3 invoke transaction with `l1_gas > 0`, `l2_gas = 0`, `l1_data_gas = 0`. This is a normal, gateway-accepted transaction shape (confirmed by the `valid_l1_gas` test case). The bug is triggered deterministically on every protobuf round-trip of such a transaction, which occurs on every P2P-syncing peer.

---

### Recommendation

Remove the value-based heuristic. The protobuf wire format should carry an explicit discriminant (e.g., a boolean `is_all_resources` flag, or a separate oneof) so the deserialiser can reconstruct the exact variant without inspecting field values. Until the wire format is updated, the converter should default to `AllResources` whenever all three gas-bound fields are present in the message, reserving `L1Gas` only for messages that explicitly omit `l2_gas` and `l1_data_gas` (i.e., the legacy 0.13.2 format indicated by the existing `TODO` comment). [6](#0-5) 

---

### Proof of Concept

```
1. User submits InvokeV3 with:
       resource_bounds = AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }

2. Gateway calls convert_rpc_tx_to_internal → InternalRpcInvokeTransactionV3
   resource_bounds field is AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }
   InvokeTransactionV3Trait::resource_bounds() returns AllResources(...)

3. get_invoke_transaction_v3_hash calls get_tip_resource_bounds_hash(AllResources(...), tip)
   resource_felts = [L1_GAS_felt, L2_GAS_felt(0), L1_DATA_GAS_felt(0)]   ← 3 elements
   H_original = Poseidon(tip, L1_GAS_felt, L2_GAS_felt(0), L1_DATA_GAS_felt(0))

4. Transaction is included in a block and propagated via P2P protobuf.

5. Peer deserialises protobuf::InvokeV3 → InvokeTransactionV3 via
   TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
       l2_gas.is_zero() && l1_data_gas.is_zero()  →  L1Gas(X)   ← WRONG variant

6. Peer calls get_invoke_transaction_v3_hash on the deserialized tx:
   get_tip_resource_bounds_hash(L1Gas(X), tip)
   resource_felts = [L1_GAS_felt, L2_GAS_felt(0)]                ← only 2 elements
   H_peer = Poseidon(tip, L1_GAS_felt, L2_GAS_felt(0))

7. H_peer ≠ H_original → validate_transaction_hash returns false
   → peer rejects the block / transaction
``` [7](#0-6) [8](#0-7)

### Citations

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L593-660)
```rust
impl TryFrom<protobuf::InvokeV3> for InvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::InvokeV3) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;

        let tip = Tip(value.tip);

        let signature = TransactionSignature(
            value
                .signature
                .ok_or(missing("InvokeV3::signature"))?
                .parts
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?
                .into(),
        );

        let nonce = Nonce(value.nonce.ok_or(missing("InvokeV3::nonce"))?.try_into()?);

        let sender_address = value.sender.ok_or(missing("InvokeV3::sender"))?.try_into()?;

        let calldata =
            value.calldata.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?;

        let calldata = Calldata(calldata.into());

        let nonce_data_availability_mode =
            enum_int_to_volition_domain(value.nonce_data_availability_mode)?;

        let fee_data_availability_mode =
            enum_int_to_volition_domain(value.fee_data_availability_mode)?;

        let paymaster_data = PaymasterData(
            value.paymaster_data.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?,
        );

        let account_deployment_data = AccountDeploymentData(
            value
                .account_deployment_data
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?,
        );

        let proof_facts: ProofFacts = value
            .proof_facts
            .into_iter()
            .map(Felt::try_from)
            .collect::<Result<Vec<_>, _>>()?
            .into();

        Ok(Self {
            resource_bounds,
            tip,
            signature,
            nonce,
            sender_address,
            calldata,
            nonce_data_availability_mode,
            fee_data_availability_mode,
            paymaster_data,
            account_deployment_data,
            proof_facts,
        })
    }
```

**File:** crates/starknet_api/src/transaction/fields.rs (L363-420)
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

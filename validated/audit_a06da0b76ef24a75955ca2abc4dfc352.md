### Title
Protobuf `ValidResourceBounds` Round-Trip Destroys Type Discriminant, Producing Divergent Transaction Hash - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` silently converts `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` into `L1Gas(X)`. Because `get_tip_resource_bounds_hash` hashes a different number of elements for each variant (3 resource bounds for `AllResources`, 2 for `L1Gas`), the transaction hash computed after a protobuf round-trip diverges from the hash computed at submission time. A transaction signed and admitted by the gateway under hash H_all is re-identified as H_l1 ≠ H_all by any node that receives it over P2P, causing valid transactions to be rejected or bound to the wrong hash.

### Finding Description

**Step 1 – Submission path always uses `AllResources`.**

`RpcInvokeTransactionV3.resource_bounds` is typed `AllResourceBounds` (not `ValidResourceBounds`). The gateway converts it to `InternalRpcInvokeTransactionV3` which also carries `AllResourceBounds`. The hash is computed via `InternalRpcInvokeTransactionV3::calculate_transaction_hash` → `get_invoke_transaction_v3_hash` → `get_tip_resource_bounds_hash` with `ValidResourceBounds::AllResources(...)`. [1](#0-0) [2](#0-1) 

**Step 2 – `get_tip_resource_bounds_hash` produces different-length Poseidon inputs per variant.**

```
L1Gas(X):        Poseidon(tip, L1Gas_packed(X), L2Gas_packed(0))          // 3 elements
AllResources(X,0,0): Poseidon(tip, L1Gas_packed(X), L2Gas_packed(0), L1DataGas_packed(0)) // 4 elements
``` [3](#0-2) 

**Step 3 – Protobuf serializer emits identical bytes for both variants when l2_gas=0 and l1_data_gas=0.**

`From<ValidResourceBounds> for protobuf::ResourceBounds` serializes `AllResources(X,0,0)` and `L1Gas(X)` to the same wire format: `{l1_gas: X, l2_gas: 0, l1_data_gas: 0}`. [4](#0-3) 

**Step 4 – Protobuf deserializer converts back to `L1Gas` when l2_gas=0 and l1_data_gas=0.**

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← type discriminant lost
} else {
    ValidResourceBounds::AllResources(...)
})
``` [5](#0-4) 

**Step 5 – The round-trip is lossy: `AllResources(X,0,0)` → protobuf → `L1Gas(X)`.**

Any node that receives the transaction over P2P and recomputes the hash from the deserialized struct obtains H_l1 ≠ H_all. The user's signature was computed over H_all; signature verification against H_l1 fails.

**Step 6 – The gateway accepts transactions with `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)`.**

The stateless validator's `validate_resource_bounds` only rejects a transaction if `max_possible_fee(Tip::ZERO) == 0` or if `l2_gas.max_price_per_unit < min_gas_price`. With `min_gas_price = 0` (the default in tests, confirmed by the `valid_l1_gas` test case), a transaction with only l1_gas bounds passes all checks. [6](#0-5) [7](#0-6) 

### Impact Explanation

Any node receiving the transaction via P2P computes H_l1 ≠ H_all. Depending on whether the receiving node verifies the hash before admission:

- **Hash verification present:** the transaction is rejected as having an invalid hash, even though it was legitimately signed and admitted by the originating node. This matches **High – Mempool/gateway/RPC admission rejects valid transactions before sequencing**.
- **Hash verification absent:** the transaction is stored under H_l1 while the signature covers H_all. Signature verification at execution time fails, or the block commits a transaction under the wrong hash. This matches **High – Transaction conversion or signature/hash logic binds the wrong hash**.

### Likelihood Explanation

The trigger requires only that a user submit a V3 transaction with non-zero l1_gas and zero l2_gas and l1_data_gas — a natural pattern for users who only care about L1 execution cost. The gateway accepts such transactions when `min_gas_price = 0`. The protobuf path is exercised on every P2P transaction propagation.

### Recommendation

In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, only fall back to `L1Gas` when `l1_data_gas` is **absent** from the wire message (the pre-0.13.2 backward-compatibility case), not when it is present but zero:

```rust
let l1_data_gas_opt = value.l1_data_gas;
// ...
Ok(match l1_data_gas_opt {
    None if l2_gas.is_zero() => ValidResourceBounds::L1Gas(l1_gas),
    _ => ValidResourceBounds::AllResources(AllResourceBounds {
        l1_gas,
        l2_gas,
        l1_data_gas: l1_data_gas_opt.map(|v| v.try_into()).transpose()?.unwrap_or_default(),
    }),
})
```

This preserves the `AllResources` discriminant for all new transactions while retaining backward compatibility with 0.13.2 messages that omit `l1_data_gas` entirely.

### Proof of Concept

```rust
use starknet_api::transaction::fields::{
    AllResourceBounds, ResourceBounds, ValidResourceBounds, GasAmount, GasPrice,
};
use starknet_api::transaction_hash::get_tip_resource_bounds_hash;
use starknet_api::transaction::fields::Tip;

let l1_gas = ResourceBounds { max_amount: GasAmount(100), max_price_per_unit: GasPrice(1) };
let zero   = ResourceBounds::default();

// What the user signs (AllResources with zero l2/l1_data):
let all = ValidResourceBounds::AllResources(AllResourceBounds {
    l1_gas, l2_gas: zero, l1_data_gas: zero,
});
let h_all = get_tip_resource_bounds_hash(&all, &Tip(0)).unwrap();

// What the P2P deserializer produces after round-trip:
let l1 = ValidResourceBounds::L1Gas(l1_gas);
let h_l1 = get_tip_resource_bounds_hash(&l1, &Tip(0)).unwrap();

assert_ne!(h_all, h_l1);  // ← passes: hashes diverge
```

The `assert_ne!` passes because `h_all` is `Poseidon(0, L1Gas_packed, L2Gas_packed(0), L1DataGas_packed(0))` (4 elements) while `h_l1` is `Poseidon(0, L1Gas_packed, L2Gas_packed(0))` (3 elements). Any transaction submitted with this resource-bounds pattern will have its hash silently changed by the protobuf round-trip. [3](#0-2) [5](#0-4) [4](#0-3)

### Citations

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L669-677)
```rust
impl TransactionHasher for InternalRpcInvokeTransactionV3 {
    fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
        transaction_version: &TransactionVersion,
    ) -> Result<TransactionHash, StarknetApiError> {
        get_invoke_transaction_v3_hash(self, chain_id, transaction_version)
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

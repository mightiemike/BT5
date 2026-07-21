### Title
Protobuf `ValidResourceBounds` Conversion Silently Downgrades `AllResources` V3 Transactions to `L1Gas`, Producing a Wrong Transaction Hash - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf-to-Rust conversion for `ResourceBounds` in the state-sync P2P path incorrectly classifies a V3 `AllResources` transaction as `L1Gas` whenever both `l2_gas` and `l1_data_gas` are zero. Because `get_tip_resource_bounds_hash` hashes a different number of elements for `L1Gas` (3: tip + L1 + L2) versus `AllResources` (4: tip + L1 + L2 + L1_data), the transaction hash recomputed from the deserialized object diverges from the canonical hash that was committed to the block. This is a direct analog of the external report's pattern: a "zero" sentinel overwrites a correctly-computed value, causing a downstream function to operate on the wrong representation.

### Finding Description

**Root cause — `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`** [1](#0-0) 

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← wrong variant
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

A V3 (`AllResources`) transaction whose user set `l2_gas = 0` and `l1_data_gas = 0` (a valid, accepted configuration — the gateway allows any single non-zero resource bound) is silently re-typed to `L1Gas` on the receiving side.

**Hash divergence — `get_tip_resource_bounds_hash`** [2](#0-1) 

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 3-element hash
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 4-element hash
    }
});
```

`L1Gas` produces `poseidon(tip, L1_packed, L2_packed)` — three elements.  
`AllResources` produces `poseidon(tip, L1_packed, L2_packed, L1_data_packed)` — four elements.  
Even when `l2_gas = 0` and `l1_data_gas = 0`, the two Poseidon outputs are numerically different.

**Canonical hash is computed with `AllResources` at submission time** [3](#0-2) 

The gateway calls `tx_without_hash.calculate_transaction_hash(&self.chain_id)` on the `InternalRpcInvokeTransactionV3`, whose `resource_bounds()` always returns `ValidResourceBounds::AllResources`. [4](#0-3) 

So the hash committed to the block is the 4-element Poseidon hash.

**State-sync path stores the wrong variant** [5](#0-4) 

`TryFrom<protobuf::TransactionInBlock> for (Transaction, TransactionHash)` reads the hash verbatim from the wire but reconstructs the `InvokeTransactionV3` (which carries `resource_bounds: ValidResourceBounds`) through the buggy converter. The stored object now has `L1Gas` while the stored hash was computed with `AllResources`.

**OS Cairo always asserts exactly 3 resource bounds** [6](#0-5) 

```cairo
assert n_resource_bounds = 3;
```

When the blockifier/SNOS populates hints from the stored `InvokeTransactionV3` with `L1Gas` (only 2 resource bounds), the OS hash computation diverges from the stored hash, causing SNOS execution to produce a wrong transaction hash or abort.

### Impact Explanation

A syncing or validating node that receives a V3 invoke transaction with `l2_gas = 0` and `l1_data_gas = 0` over the state-sync P2P path will:

1. Store the transaction with `ValidResourceBounds::L1Gas` instead of `AllResources`.
2. Recompute a transaction hash that differs from the canonical hash committed to the block.
3. Fail `validate_transaction_hash` checks, or — more critically — cause the SNOS to compute a wrong transaction hash during block re-execution, producing wrong receipts, wrong events, and wrong state commitments.

This matches the **Critical** impact category: *Wrong state, receipt, event, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input*, and the **High** category: *Transaction conversion or signature/hash logic binds the wrong hash or executable payload*.

### Likelihood Explanation

The trigger is any V3 transaction (Invoke, Declare, DeployAccount) where the user sets `l2_gas.max_amount = 0`, `l2_gas.max_price_per_unit = 0`, `l1_data_gas.max_amount = 0`, `l1_data_gas.max_price_per_unit = 0`. The gateway's stateless validator explicitly accepts transactions with only one non-zero resource bound (e.g., `valid_l1_gas` test case). Such transactions are therefore reachable by any unprivileged user.

### Recommendation

Remove the heuristic downgrade. A transaction that arrived as a V3 `AllResources` transaction must remain `AllResources` regardless of whether the individual bounds are zero. The `l1_data_gas` field being absent in the protobuf message (legacy 0.13.2 wire format) is the only legitimate reason to fall back to `L1Gas`; that case is already handled by `unwrap_or_default()`. The fix is:

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
        let l1_gas: ResourceBounds = l1_gas.try_into()?;
        let l2_gas: ResourceBounds = l2_gas.try_into()?;

-       // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
-       let l1_data_gas = value.l1_data_gas.unwrap_or_default();
-       let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
-       Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
-           ValidResourceBounds::L1Gas(l1_gas)
-       } else {
-           ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
-       })

+       // If l1_data_gas is absent this is a legacy 0.13.2 wire message → L1Gas.
+       // Otherwise it is always AllResources, even when the bounds are zero.
+       Ok(match value.l1_data_gas {
+           None => ValidResourceBounds::L1Gas(l1_gas),
+           Some(l1_data_gas) => {
+               let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
+               ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
+           }
+       })
    }
}
```

### Proof of Concept

```rust
use starknet_api::transaction::fields::{
    AllResourceBounds, GasAmount, GasPrice, ResourceBounds, ValidResourceBounds,
};
use apollo_protobuf::protobuf;

fn main() {
    // Construct a protobuf ResourceBounds for a V3 AllResources transaction
    // where l2_gas and l1_data_gas are both zero (valid user choice).
    let proto = protobuf::ResourceBounds {
        l1_gas: Some(protobuf::ResourceLimits {
            max_amount: 1000,
            max_price_per_unit: Some(Felt::from(1u64).into()),
        }),
        l2_gas: Some(protobuf::ResourceLimits {
            max_amount: 0,
            max_price_per_unit: Some(Felt::ZERO.into()),
        }),
        l1_data_gas: Some(protobuf::ResourceLimits {  // present → AllResources
            max_amount: 0,
            max_price_per_unit: Some(Felt::ZERO.into()),
        }),
    };

    let converted = ValidResourceBounds::try_from(proto).unwrap();

    // BUG: converted is L1Gas, not AllResources
    assert!(matches!(converted, ValidResourceBounds::L1Gas(_)),
        "Bug confirmed: AllResources silently downgraded to L1Gas");

    // Hash computed with AllResources (4 elements) ≠ hash computed with L1Gas (3 elements)
    let all_resources = ValidResourceBounds::AllResources(AllResourceBounds {
        l1_gas: ResourceBounds { max_amount: GasAmount(1000), max_price_per_unit: GasPrice(1) },
        l2_gas: ResourceBounds::default(),
        l1_data_gas: ResourceBounds::default(),
    });
    let tip = starknet_api::transaction::fields::Tip(0);
    let h_correct = get_tip_resource_bounds_hash(&all_resources, &tip).unwrap();
    let h_wrong   = get_tip_resource_bounds_hash(&converted,     &tip).unwrap();
    assert_ne!(h_correct, h_wrong, "Hash mismatch confirmed");
}
```

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L134-184)
```rust
impl TryFrom<protobuf::TransactionInBlock> for (Transaction, TransactionHash) {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::TransactionInBlock) -> Result<Self, Self::Error> {
        let tx_hash = value
            .transaction_hash
            .clone()
            .ok_or(missing("Transaction::transaction_hash"))?
            .try_into()
            .map(TransactionHash)?;
        let txn = value.txn.ok_or(missing("Transaction::txn"))?;
        let transaction: Transaction = match txn {
            protobuf::transaction_in_block::Txn::DeclareV0(declare_v0) => Transaction::Declare(
                DeclareTransaction::V0(DeclareTransactionV0V1::try_from(declare_v0)?),
            ),
            protobuf::transaction_in_block::Txn::DeclareV1(declare_v1) => Transaction::Declare(
                DeclareTransaction::V1(DeclareTransactionV0V1::try_from(declare_v1)?),
            ),
            protobuf::transaction_in_block::Txn::DeclareV2(declare_v2) => Transaction::Declare(
                DeclareTransaction::V2(DeclareTransactionV2::try_from(declare_v2)?),
            ),
            protobuf::transaction_in_block::Txn::DeclareV3(declare_v3) => Transaction::Declare(
                DeclareTransaction::V3(DeclareTransactionV3::try_from(declare_v3)?),
            ),
            protobuf::transaction_in_block::Txn::Deploy(deploy) => {
                Transaction::Deploy(DeployTransaction::try_from(deploy)?)
            }
            protobuf::transaction_in_block::Txn::DeployAccountV1(deploy_account_v1) => {
                Transaction::DeployAccount(DeployAccountTransaction::V1(
                    DeployAccountTransactionV1::try_from(deploy_account_v1)?,
                ))
            }
            protobuf::transaction_in_block::Txn::DeployAccountV3(deploy_account_v3) => {
                Transaction::DeployAccount(DeployAccountTransaction::V3(
                    DeployAccountTransactionV3::try_from(deploy_account_v3)?,
                ))
            }
            protobuf::transaction_in_block::Txn::InvokeV0(invoke_v0) => Transaction::Invoke(
                InvokeTransaction::V0(InvokeTransactionV0::try_from(invoke_v0)?),
            ),
            protobuf::transaction_in_block::Txn::InvokeV1(invoke_v1) => Transaction::Invoke(
                InvokeTransaction::V1(InvokeTransactionV1::try_from(invoke_v1)?),
            ),
            protobuf::transaction_in_block::Txn::InvokeV3(invoke_v3) => Transaction::Invoke(
                InvokeTransaction::V3(InvokeTransactionV3::try_from(invoke_v3)?),
            ),
            protobuf::transaction_in_block::Txn::L1Handler(l1_handler) => {
                Transaction::L1Handler(L1HandlerTransaction::try_from(l1_handler)?)
            }
        };
        Ok((transaction, tx_hash))
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L123-125)
```text
    with_attr error_message("Invalid number of resource bounds: {n_resource_bounds}.") {
        assert n_resource_bounds = 3;
    }
```

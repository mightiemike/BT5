### Title
`ValidResourceBounds` Protobuf Deserialization Silently Downcasts `AllResources` to `L1Gas` Variant, Causing Consensus Block Rejection — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a value-based heuristic to distinguish the `L1Gas` variant (pre-0.13.3) from the `AllResources` variant (post-0.13.3). Any `AllResources` transaction whose `l2_gas` and `l1_data_gas` bounds are both zero is silently re-classified as `L1Gas` on deserialization. Because the RPC/consensus transaction types (`RpcInvokeTransactionV3`, `RpcDeclareTransactionV3`, `RpcDeployAccountTransactionV3`) require `AllResources` and hard-reject `L1Gas`, a validator that receives such a transaction over the consensus P2P channel will fail to deserialize the `TransactionBatch` and reject the block proposal. The gateway has no minimum-gas-amount guard that would prevent such transactions from entering the mempool.

### Finding Description

**Root cause — `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`** [1](#0-0) 

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← wrong variant
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

The check is purely value-based: it has no way to know whether the sender intended `L1Gas` (a pre-0.13.3 transaction) or `AllResources` with zero L2/data-gas bounds (a valid post-0.13.3 transaction). The protobuf wire format carries no version tag for this field.

**Serialization path — `From<ValidResourceBounds> for protobuf::ResourceBounds`** [2](#0-1) 

When an `AllResources` value with `l2_gas = 0` and `l1_data_gas = 0` is serialized, both optional fields are written as zero `ResourceLimits` messages. On the return trip the deserializer sees two zero fields and produces `L1Gas`.

**Downstream rejection — `TryFrom<InvokeTransactionV3> for RpcInvokeTransaction

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

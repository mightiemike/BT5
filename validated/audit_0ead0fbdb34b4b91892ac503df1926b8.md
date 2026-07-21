### Title
Protobuf `ValidResourceBounds` deserialization silently downgrades `AllResources` to `L1Gas` when `l2_gas` and `l1_data_gas` are zero, producing a divergent transaction hash - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion in `crates/apollo_protobuf/src/converters/transaction.rs` produces `ValidResourceBounds::L1Gas` instead of `ValidResourceBounds::AllResources` whenever both `l2_gas` and `l1_data_gas` are zero — even for a legitimately-created 0.13.3+ V3 transaction that was signed and hashed as `AllResources`. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` term only for `AllResources`, the hash computed after protobuf round-trip differs from the hash computed at the gateway. This is the sequencer analog of the NounsDAO bug: a state/type check uses an uninitialized (wrong) representation of an object, causing a downstream invariant — here the canonical transaction hash — to be silently violated.

---

### Finding Description

**The type boundary**

`ValidResourceBounds` has two variants:

```
ValidResourceBounds::L1Gas(ResourceBounds)          // pre-0.13.3, 2-resource hash
ValidResourceBounds::AllResources(AllResourceBounds) // 0.13.3+,   3-resource hash
``` [1](#0-0) 

The hash function `get_tip_resource_bounds_hash` branches on this variant:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 felts
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 felts
    }
});
``` [2](#0-1) 

**The broken conversion**

When a block is synced via P2P, `InvokeTransactionV3` (which carries `ValidResourceBounds`) is deserialized from protobuf through:

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)          // ← wrong variant for 0.13.3+ tx
        } else {
            ValidResourceBounds::AllResources(...)
        })
    }
}
``` [3](#0-2) 

A valid 0.13.3+ V3 transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is serialized to protobuf with both fields present but zero. On deserialization the zero-check fires and the variant is silently changed to `L1Gas`. The `AllResources` → `L1Gas` downgrade is irreversible: the `l1_data_gas` field is discarded.

**Contrast with the RPC/mempool path**

The mempool P2P path uses a separate, correct converter that always produces `AllResourceBounds`:

```rust
impl TryFrom<protobuf::ResourceBounds> for AllResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        Ok(Self {
            l1_gas:      value.l1_gas.ok_or(...)?.try_into()?,
            l2_gas:      value.l2_gas.ok_or(...)?.try_into()?,
            l1_data_gas: value.l1_data_gas.ok_or(...)?.try_into()?,
        })
    }
}
``` [4](#0-3) 

The block-sync path uses the broken `ValidResourceBounds` converter; the mempool path uses the correct `AllResourceBounds` converter. The same transaction therefore has two different canonical representations depending on which path it traverses.

**Hash divergence**

`InternalRpcInvokeTransactionV3` (gateway/mempool) always wraps its `AllResourceBounds` as `AllResources` when computing the hash:

```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)  // always AllResources
    }
}
``` [5](#0-4) 

After protobuf round-trip the same transaction is represented as `L1Gas`. `get_tip_resource_bounds_hash` then produces a 2-felt preimage instead of a 3-felt preimage, yielding a different Poseidon digest. The stored `tx_hash` (computed at ingestion as `AllResources`) no longer matches the hash recomputable from the stored `InvokeTransactionV3`.

**RPC hard failure**

The conversion from the stored `InvokeTransactionV3` back to `RpcInvokeTransactionV3` (used by every RPC endpoint that returns transaction data) explicitly rejects `L1Gas`:

```rust
impl TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3 {
    fn try_from(value: InvokeTransactionV3) -> Result<Self, Self::Error> {
        Ok(Self {
            resource_bounds: match value.resource_bounds {
                ValidResourceBounds::AllResources(bounds) => bounds,
                _ => return Err(StarknetApiError::OutOfRange { string: "resource_bounds" }),
            },
            ...
        })
    }
}
``` [6](#0-5) 

Any node that synced the block via P2P will return an error from `starknet_getTransactionByHash`, `starknet_getTransactionReceipt`, `starknet_simulateTransactions`, and fee-estimation endpoints for every affected transaction.

---

### Impact Explanation

**High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

Any V3 transaction included

### Citations

**File:** crates/starknet_api/src/transaction/fields.rs (L363-367)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L202-210)
```rust
    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L586-612)
```rust
impl TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3 {
    type Error = StarknetApiError;

    fn try_from(value: InvokeTransactionV3) -> Result<Self, Self::Error> {
        Ok(Self {
            resource_bounds: match value.resource_bounds {
                ValidResourceBounds::AllResources(bounds) => bounds,
                _ => {
                    return Err(StarknetApiError::OutOfRange {
                        string: "resource_bounds".to_string(),
                    });
                }
            },
            signature: value.signature,
            nonce: value.nonce,
            tip: value.tip,
            paymaster_data: value.paymaster_data,
            nonce_data_availability_mode: value.nonce_data_availability_mode,
            fee_data_availability_mode: value.fee_data_availability_mode,
            sender_address: value.sender_address,
            calldata: value.calldata,
            account_deployment_data: value.account_deployment_data,
            proof_facts: value.proof_facts,
            proof: Proof::default(),
        })
    }
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

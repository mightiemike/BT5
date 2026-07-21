### Title
P2P Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Divergent Transaction Hash — (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf-to-`ValidResourceBounds` conversion used in the P2P state-sync path applies a value-based heuristic: if `l2_gas` and `l1_data_gas` are both zero it emits `ValidResourceBounds::L1Gas`, otherwise `ValidResourceBounds::AllResources`. Because `get_tip_resource_bounds_hash` hashes a **different number of resource-bound elements** for the two variants (2 for `L1Gas`, 3 for `AllResources`), any post-0.13.3 `AllResources` transaction whose `l2_gas` and `l1_data_gas` happen to be zero will have its hash recomputed incorrectly on the receiving node, breaking the hash-canonicalization invariant.

---

### Finding Description

**Root cause — wrong variant selection in protobuf deserialization**

`crates/apollo_protobuf/src/converters/transaction.rs` lines 431–435:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

The heuristic conflates two semantically distinct cases:
- A **pre-0.13.3** `L1Gas` transaction, which was always signed with only 2 resource elements in the hash preimage.
- A **post-0.13.3** `AllResources` transaction that legitimately sets `l2_gas = 0` and `l1_data_gas = 0` (e.g., a transaction that only consumes L1 gas). This transaction was signed with 3 resource elements.

The `InternalRpcInvokeTransactionV3` type always stores `resource_bounds: AllResourceBounds` and always wraps it as `ValidResourceBounds::AllResources` when converting to `InvokeTransactionV3`:

```rust
impl From<InternalRpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: InternalRpcInvokeTransactionV3) -> Self {
        Self {
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            ...
        }
    }
}
``` [2](#0-1) 

So the canonical hash is always computed with `AllResources`. But the P2P sync path deserializes the same transaction back as `L1Gas` when the two fields are zero.

**Hash divergence — `get_tip_resource_bounds_hash`**

`crates/starknet_api/src/transaction_hash.rs` lines 203–208:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
``` [3](#0-2) 

- `L1Gas` → `poseidon(tip, L1_GAS_packed, L2_GAS_packed(0))` — **2 resource elements**
- `AllResources` with zero l2/l1_data → `poseidon(tip, L1_GAS_packed, L2_GAS_packed(0), L1_DATA_GAS_packed(0))` — **3 resource elements**

These produce **different field elements**, so the full transaction hash diverges.

The consensus/RPC path avoids this because `RpcInvokeTransactionV3` uses `AllResourceBounds` directly and a separate converter (`AllResourceBounds::try_from`) that never produces `L1Gas`:

```rust
impl TryFrom<protobuf::ResourceBounds> for AllResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        Ok(Self {
            l1_gas: value.l1_gas.ok_or(...)?.try_into()?,
            l2_gas: value.l2_gas.ok_or(...)?.try_into()?,
            l1_data_gas: value.l1_data_gas.ok_or(...)?.try_into()?,
        })
    }
}
``` [4](#0-3) 

Only the `ValidResourceBounds` converter used for the `starknet_api::transaction::InvokeTransactionV3` / `DeployAccountTransactionV3` types (the P2P sync path) is affected.

---

### Impact Explanation

When a syncing node receives a block containing a V3 transaction with `AllResources` where `l2_gas = 0` and `l1_data_gas = 0`:

1. The protobuf deserializer emits `ValidResourceBounds::L1Gas`.
2. `get_tip_resource_bounds_hash` hashes only 2 resource elements.
3. The recomputed transaction hash diverges from the canonical hash committed to the block.
4. If hash validation is performed during sync, the node rejects a valid transaction — matching **"High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."**
5. If hash validation is skipped, the transaction is stored under the wrong hash — matching **"High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload"** and **"High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."**

---

### Likelihood Explanation

A post-0.13.3 `AllResources` transaction with `l2_gas = 0` and `l1_data_gas = 0` is valid at the protocol level. The gateway's `validate_resource_bounds` check only requires at least one non-zero bound; it does not require `l2_gas` to be non-zero. Any user who submits such a transaction (e.g., a pure L1-gas transaction using the new V3 format) triggers the divergence on every node that syncs the block via P2P.

---

### Recommendation

Remove the value-based heuristic. The protobuf `ResourceBounds` message already carries all three fields. The correct fix is to always produce `AllResources` when deserializing from protobuf (matching the behavior of `AllResourceBounds::try_from`), and only produce `L1Gas` when an explicit version/type tag in the protobuf message or the surrounding block context indicates a pre-0.13.3 transaction. Alternatively, align the `ValidResourceBounds` converter with the `AllResourceBounds` converter already used in the RPC/consensus path:

```rust
// Proposed fix: always AllResources from protobuf
Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))
``` [5](#0-4) 

---

### Proof of Concept

1. Craft a V3 Invoke transaction with `AllResources { l1_gas: {max_amount: N, max_price: P}, l2_gas: zero, l1_data_gas: zero }` and submit it via the RPC gateway.
2. The gateway accepts it (l1_gas is non-zero). The `InternalRpcInvokeTransactionV3` stores `AllResourceBounds`. The hash is computed as `poseidon(INVOKE, version, sender, poseidon(tip, L1_GAS_packed, L2_GAS_packed(0), L1_DATA_GAS_packed(0)), ...)`.
3. The transaction is included in a block. The block is propagated via P2P.
4. A syncing node deserializes the `InvokeTransactionV3` from protobuf. The `ValidResourceBounds::try_from` heuristic fires: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas(l1_gas)`.
5. The syncing node recomputes the hash as `poseidon(INVOKE, version, sender, poseidon(tip, L1_GAS_packed, L2_GAS_packed(0)), ...)` — **missing the `L1_DATA_GAS_packed(0)` element**.
6. The two hashes differ. The syncing node either rejects the transaction (sync failure) or stores it under the wrong hash (wrong RPC output).

<cite repo="

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

**File:** crates/starknet_api/src/transaction_hash.rs (L203-208)
```rust
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });
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

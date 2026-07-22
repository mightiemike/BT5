### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas` on Zero Bounds, Producing a Different Transaction Hash - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

When a V3 transaction carrying `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is serialized to protobuf and then deserialized on a receiving node, the `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter silently downgrades the variant from `ValidResourceBounds::AllResources` to `ValidResourceBounds::L1Gas`. Because `get_tip_resource_bounds_hash` hashes a different number of resource elements depending on the variant (2 for `L1Gas`, 3 for `AllResources`), the transaction hash computed on the receiving node diverges from the hash computed on the originating node. Any node that re-derives the hash after protobuf round-trip will bind a different hash to the same transaction bytes.

### Finding Description

**Root cause — protobuf deserialization:**

```rust
// crates/apollo_protobuf/src/converters/transaction.rs  line 431
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant silently changed
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

**Why the variant matters for hashing:**

`get_tip_resource_bounds_hash` branches on the variant and produces a structurally different Poseidon input:

```rust
// crates/starknet_api/src/transaction_hash.rs  lines 203-208
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2-element hash
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3-element hash
    }
});
``` [2](#0-1) 

**How the originating node computes the hash:**

`InternalRpcInvokeTransactionV3` stores `resource_bounds: AllResourceBounds`. Its conversion to `InvokeTransactionV3` unconditionally wraps it in `ValidResourceBounds::AllResources`:

```rust
// crates/starknet_api/src/rpc_transaction.rs  line 682
resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
``` [3](#0-2) 

So even when `l2_gas = 0` and `l1_data_gas = 0`, the originating node computes a **3-element** Poseidon hash (tip + L1_GAS + L2_GAS + L1_DATA_GAS).

**How the receiving node computes the hash:**

After protobuf round-trip, the same bounds become `ValidResourceBounds::L1Gas`, so the receiving node computes a **2-element** Poseidon hash (tip + L1_GAS + L2_GAS). The two hashes are cryptographically distinct.

**Serialization is lossless — the downgrade is one-way:**

The serializer preserves all three fields:

```rust
// crates/apollo_protobuf/src/converters/transaction.rs  lines 479-487
ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
    protobuf::ResourceBounds {
        l1_gas: Some(l1_gas.into()),
        l2_gas: Some(l2_gas.into()),
        l1_data_gas: Some(l1_data_gas.into()),
    },
``` [4](#0-3) 

The wire bytes are identical for both variants when the values are zero; only the deserializer's type decision differs.

**The same zero-check also exists in the RPC layer:**

```rust
// crates/apollo_rpc/src/v0_8/transaction.rs  lines 190-198
if value.l1_data_gas.is_zero() && value.l2_gas.is_zero() {
    Self::L1Gas(value.l1_gas)
} else {
    Self::AllResources(...)
}
``` [5](#0-4) 

However, the RPC path operates on `ResourceBoundsMapping` (a JSON-level type), while the gateway and consensus paths operate on `AllResourceBounds` directly — so the RPC-layer guard does not protect the P2P/sync path.

### Impact Explanation

Any node that re-derives the transaction hash after receiving a block over P2P (e.g., during state sync or consensus validation) will compute a hash that does not match the hash stored in the block. This causes:

- **Wrong hash bound to the transaction**: the receiving node associates a different `TransactionHash` with the same transaction bytes, violating the canonical hash invariant.
- **Block/transaction rejection**: `validate_transaction_hash` compares the stored hash against the recomputed hash; a mismatch causes the block to be rejected.
- **Chain split**: the sequencer node accepts and commits a block that peer nodes reject, breaking consensus. [6](#0-5) 

### Likelihood Explanation

Any V3 invoke transaction submitted with `l2_gas = 0` and `l1_data_gas = 0` (a valid and common configuration for users who do not want to pay L2/data-gas fees) triggers this path. No special privilege is required; a normal user transaction is sufficient. The condition is hit on every P2P sync of such a transaction.

### Recommendation

Remove the zero-value type downgrade from the protobuf deserializer. The variant must be preserved across serialization boundaries. One approach: add an explicit discriminant field to the protobuf `ResourceBounds` message (e.g., `bool all_resources`) so the deserializer can reconstruct the correct variant without inspecting values. Alternatively, always deserialize into `AllResources` when all three fields are present in the wire message, matching the behavior of `TryFrom<protobuf::ResourceBounds> for AllResourceBounds` in `crates/apollo_protobuf/src/converters/rpc_transaction.rs`:

```rust
// crates/apollo_protobuf/src/converters/rpc_transaction.rs  lines 212-223
impl TryFrom<protobuf::ResourceBounds> for AllResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        Ok(Self {
            l1_gas: value.l1_gas.ok_or(...)?.try_into()?,
            l2_gas: value.l2_gas.ok_or(...)?.try_into()?,
            l1_data_gas: value.l1_data_gas.ok_or(...)?.try_into()?,
        })
    }
}
``` [7](#0-6) 

This converter correctly never downgrades based on zero values.

### Proof of Concept

1. Submit a V3 invoke transaction with `resource_bounds = { l1_gas: { max_amount: 1000, max_price_per_unit: 1 }, l2_gas: { max_amount: 0, max_price_per_unit: 0 }, l1_data_gas: { max_amount: 0, max_price_per_unit: 0 } }`.
2. The gateway stores it as `InternalRpcInvokeTransactionV3` with `AllResourceBounds`. The hash `H_orig` is computed via `get_tip_resource_bounds_hash` with `ValidResourceBounds::AllResources` → Poseidon over **4 felts** (tip, L1_GAS_packed, L2_GAS_packed=0, L1_DATA_GAS_packed=0).
3. The transaction is included in a block and serialized to protobuf for P2P sync.
4. A peer node deserializes via `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. Since `l2_gas.is_zero() && l1_data_gas.is_zero()`, the result is `ValidResourceBounds::L1Gas(l1_gas)`.
5. The peer recomputes the hash `H_peer` via `get_tip_resource_bounds_hash` with `ValidResourceBounds::L1Gas` → Poseidon over **3 felts** (tip, L1_GAS_packed, L2_GAS_packed=0). No L1_DATA_GAS term.
6. `H_orig ≠ H_peer`. The peer's `validate_transaction_hash` returns `false`, and the block is rejected.

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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L212-224)
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
}
```

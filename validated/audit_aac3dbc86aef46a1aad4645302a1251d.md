### Title
Protobuf `ValidResourceBounds` Deserialization Conflates `AllResources(X,0,0)` with `L1Gas(X)`, Producing a Different Transaction Hash — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` silently collapses `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` into `L1Gas(X)`. Because `get_tip_resource_bounds_hash` includes an extra `L1_DATA_GAS` element in the hash preimage for the `AllResources` variant but not for `L1Gas`, the two variants produce **different transaction hashes for identical underlying values**. A transaction submitted via RPC (which always produces `AllResources`) is hashed with three resource felts; after a protobuf round-trip the same transaction is hashed with two resource felts, yielding a different hash. This breaks hash-binding across the P2P/consensus boundary.

---

### Finding Description

**Step 1 – Two variants, one set of values, two different hashes.**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` always includes L1 and L2 resource felts, then conditionally appends L1_DATA_GAS only for the `AllResources` variant:

```rust
// L1 and L2 gas bounds always exist.
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];
// For new V3 txs, need to also hash the data gas bounds.
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 felts
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 felts
    }
});
Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
``` [1](#0-0) 

So:
- `L1Gas(X)` → hash over `[tip, L1_GAS_concat(X), L2_GAS_concat(0)]`
- `AllResources(X, 0, 0)` → hash over `[tip, L1_GAS_concat(X), L2_GAS_concat(0), L1_DATA_GAS_concat(0)]`

These are **different Poseidon hashes** even though the numeric values are identical.

**Step 2 – The protobuf deserializer conflates the two variants.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` maps any wire message where both `l2_gas` and `l1_data_gas` are zero to `L1Gas`, regardless of whether the sender originally used `AllResources`:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant information lost
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [2](#0-1) 

The serializer (`From<ValidResourceBounds> for protobuf::ResourceBounds`) emits the same wire bytes for both variants when l2 and l1_data are zero:

```rust
ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
    l1_gas: Some(l1_gas.into()),
    l2_gas: Some(value.get_l2_bounds().into()),   // zeros
    l1_data_gas: Some(ResourceBounds::default().into()),  // zeros
},
``` [3](#0-2) 

The round-trip `AllResources(X, 0, 0)` → protobuf → `L1Gas(X)` is lossy.

**Step 3 – The attacker-controlled entry point.**

All RPC transaction types (`RpcInvokeTransactionV3`, `RpcDeclareTransactionV3`, `RpcDeployAccountTransactionV3`) carry `AllResourceBounds` (not `ValidResourceBounds`), so they always produce the `AllResources` variant internally:

```rust
pub struct InternalRpcInvokeTransactionV3 {
    pub resource_bounds: AllResourceBounds,   // always AllResources
    ...
}
``` [4](#0-3) 

A user submitting a V3 invoke with `l2_gas = 0` and `l1_data_gas = 0` (a perfectly valid transaction) causes the gateway to compute hash **H₁** (3-felt preimage). When this `InternalRpcTransaction` is serialized to protobuf for P2P propagation and deserialized by a peer, the peer reconstructs `L1Gas(X)` and computes hash **H₂** (2-felt preimage). H₁ ≠ H₂.

**Step 4 – Divergent hash binding.**

`InternalRpcTransactionWithoutTxHash::calculate_transaction_hash` calls `get_invoke_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash`:

```rust
pub fn calculate_transaction_hash(
    &self,
    chain_id: &ChainId,
) -> Result<TransactionHash, StarknetApiError> {
    ...
    InternalRpcTransactionWithoutTxHash::Invoke(tx) =>
        tx.calculate_transaction_hash(chain_id, transaction_version)
    ...
}
``` [5](#0-4) 

The hash stored in `InternalRpcTransaction.tx_hash` on the originating node is H₁. Any peer that recomputes the hash after protobuf deserialization obtains H₂ ≠ H₁ and rejects the transaction as having an invalid hash.

---

### Impact Explanation

A valid V3 transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is assigned hash H₁ by the gateway. After protobuf round-trip across the P2P layer, every receiving peer recomputes hash H₂ ≠ H₁ and rejects the transaction. The transaction can never be sequenced by any peer that received it via P2P, even though it is cryptographically valid and correctly signed. This matches the **High** impact: "Mempool/gateway/RPC admission rejects valid transactions before sequencing" and "Transaction conversion or signature/hash logic binds the wrong hash or type."

---

### Likelihood Explanation

Any user submitting a V3 transaction with zero L2 gas and zero L1 data gas bounds triggers this path. This is a common pattern for users who do not use L2 gas or blob DA. The condition is reachable without any special privileges, simply by setting both bounds to zero in a standard RPC call.

---

### Recommendation

The protobuf deserializer must not infer the `ValidResourceBounds` variant from the numeric values of the fields. The variant is part of the transaction's signed identity and must be preserved across serialization boundaries. Two options:

1. **Add an explicit discriminant field** to the protobuf `ResourceBounds` message (e.g., a boolean `is_all_resources`) so the deserializer can reconstruct the correct variant without inspecting values.

2. **Always deserialize to `AllResources`** when all three fields are present in the wire message (which they always are, since the serializer always emits all three), and only fall back to `L1Gas` when the `l1_data_gas` field is absent (the legacy 0.13.2 case noted in the TODO comment).

The TODO comment at line 426 already acknowledges this ambiguity:
```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
``` [6](#0-5) 

Once 0.13.2 support is dropped, the deserializer should require `l1_data_gas` to be present and always produce `AllResources`, eliminating the conflation.

---

### Proof of Concept

```
1. User submits RpcInvokeTransactionV3 {
       resource_bounds: AllResourceBounds { l1_gas: 1000, l2_gas: 0, l1_data_gas: 0 },
       ...
   }

2. Gateway converts to InternalRpcInvokeTransactionV3 { resource_bounds: AllResourceBounds { ... } }
   → DeclareTransactionV3Trait::resource_bounds() returns ValidResourceBounds::AllResources(...)
   → get_tip_resource_bounds_hash hashes [tip, L1_GAS(1000), L2_GAS(0), L1_DATA_GAS(0)]  ← 3 felts
   → tx_hash = H₁

3. Node serializes to protobuf::ResourceBounds { l1_gas: 1000, l2_gas: 0, l1_data_gas: 0 }

4. Peer deserializes:
   l1_data_gas.is_zero() && l2_gas.is_zero() == true
   → ValidResourceBounds::L1Gas(1000)
   → get_tip_resource_bounds_hash hashes [tip, L1_GAS(1000), L2_GAS(0)]  ← 2 felts
   → recomputed_hash = H₂ ≠ H₁

5. Peer rejects transaction: "hash mismatch"
   → Valid transaction is permanently excluded from sequencing on all P2P peers.
```

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L124-141)
```rust
    pub fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
    ) -> Result<TransactionHash, StarknetApiError> {
        let transaction_version = &self.version();
        match self {
            InternalRpcTransactionWithoutTxHash::Declare(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
            InternalRpcTransactionWithoutTxHash::Invoke(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
            InternalRpcTransactionWithoutTxHash::DeployAccount(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
        }
    }
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L616-640)
```rust
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

impl InternalRpcInvokeTransactionV3 {
    pub fn version(&self) -> TransactionVersion {
        TransactionVersion::THREE
    }
}

impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
    fn tip(&self) -> &Tip {
```

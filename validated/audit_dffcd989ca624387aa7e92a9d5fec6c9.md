### Title
P2P Protobuf Round-Trip Silently Coerces `AllResources` to `L1Gas` Variant, Producing a Different Transaction Hash Preimage - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter in the P2P layer uses a zero-check on `l1_data_gas` and `l2_gas` to decide which `ValidResourceBounds` variant to reconstruct. When both fields are zero, it always produces `ValidResourceBounds::L1Gas`, even if the original transaction was stored as `ValidResourceBounds::AllResources`. Because `get_tip_resource_bounds_hash` produces a structurally different Poseidon hash for the two variants (2 vs. 3 resource elements), a transaction accepted by the gateway under the `AllResources` hash is re-hashed under the `L1Gas` hash on any peer that receives it via P2P, causing signature/hash verification to fail and the transaction to be rejected.

---

### Finding Description

**Serialization side** — `From<ValidResourceBounds> for protobuf::ResourceBounds`:

```rust
ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
    protobuf::ResourceBounds {
        l1_gas: Some(l1_gas.into()),
        l2_gas: Some(l2_gas.into()),
        l1_data_gas: Some(l1_data_gas.into()),   // serialized as Some(zero) when zero
    },
``` [1](#0-0) 

**Deserialization side** — `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`:

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // None → zero
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)                      // variant changed
} else {
    ValidResourceBounds::AllResources(...)
})
``` [2](#0-1) 

When a transaction carries `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` (a valid post-0.13.3 transaction where the user set both to zero), the round-trip through protobuf produces `L1Gas(X)`. The variant change is invisible to the caller.

**Hash divergence** — `get_tip_resource_bounds_hash` branches on the variant:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2-element hash
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3-element hash
    }
});
Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
``` [3](#0-2) 

- `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` → `poseidon(tip, L1_GAS_felt, L2_GAS_zero_felt, L1_DATA_GAS_zero_felt)` (3 elements)
- `L1Gas(X)` → `poseidon(tip, L1_GAS_felt, L2_GAS_zero_felt)` (2 elements)

These are distinct Poseidon outputs. The transaction hash committed at the gateway differs from the hash recomputed on any receiving peer.

**P2P flow** — the transaction submission diagram confirms the exact path:

```
User → Gateway A (validates, stores AllResources hash) → Mempool → P2P Propagator
                                                                         ↓
                                                              P2P Runner → Gateway B
                                                              (protobuf decode → L1Gas hash)
                                                              → signature check fails → rejected
```



The `RpcTransaction` type used at the RPC layer always carries `AllResourceBounds` (not `ValidResourceBounds`), so every transaction submitted via the public API is stored as `AllResources`. [4](#0-3) 

The gateway converts it to `ValidResourceBounds::AllResources(tx.resource_bounds)` unconditionally. [5](#0-4) 

---

### Impact Explanation

A user who submits a V3 transaction with `l2_gas = 0` and `l1_data_gas = 0` (a legitimate old-style V3 transaction submitted through the modern RPC path) signs over the `AllResources` Poseidon hash (3 elements). The gateway accepts it. When the transaction is broadcast via P2P, every receiving node deserializes it as `L1Gas`, recomputes the hash with 2 elements, finds a mismatch against the stored hash, and rejects the transaction. The transaction can never be sequenced on any node other than the originating sequencer, breaking network consistency and preventing finalization.

This matches: **High — Mempool/gateway/RPC admission rejects valid transactions before sequencing.**

---

### Likelihood Explanation

The trigger requires only a normally-submitted V3 transaction with zero `l2_gas` and `l1_data_gas` bounds — a configuration that is valid per the protocol (old-style V3 transactions). No privileged access, no malformed bytes, and no malicious peer are required; the bug fires on every normal P2P propagation of such a transaction. The TODO comment in the code (`// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2`) confirms the coercion is a known temporary measure, not an intentional canonicalization. [6](#0-5) 

---

### Recommendation

The deserialization must preserve the original variant. Two options:

1. **Strict**: Once 0.13.2 support is dropped (as the TODO intends), reject any message where `l1_data_gas` is `None` rather than silently defaulting to zero.
2. **Immediate**: Carry an explicit variant discriminator in the protobuf message (e.g., a boolean `is_all_resources` flag or a separate oneof), so the decoder can reconstruct the exact variant without relying on value-based inference.

Either way, the zero-check heuristic must not be used to select the variant, because `AllResources { l2_gas: 0, l1_data_gas: 0 }` and `L1Gas` are semantically distinct hash domains.

---

### Proof of Concept

1. User constructs a V3 invoke transaction with `resource_bounds = { l1_gas: { max_amount: 1000, max_price_per_unit: 1 }, l2_gas: { max_amount: 0, max_price_per_unit: 0 }, l1_data_gas: { max_amount: 0, max_price_per_unit: 0 } }`.
2. User computes the hash via `get_invoke_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash` with `AllResources` → 3-element Poseidon hash. User signs this hash.
3. User submits via RPC. Gateway A converts to `ValidResourceBounds::AllResources(...)`, verifies signature against the 3-element hash → **accepted**.
4. Mempool propagates to `MempoolP2pPropagator`, which serializes via `From<ValidResourceBounds> for protobuf::ResourceBounds` → `{ l1_gas: Some(X), l2_gas: Some(0), l1_data_gas: Some(0) }`.
5. Peer node deserializes via `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`: `l1_data_gas.unwrap_or_default() = 0`, `l2_gas = 0` → `ValidResourceBounds::L1Gas(X)`.
6. Peer's gateway recomputes hash via `get_tip_resource_bounds_hash` with `L1Gas` → 2-element Poseidon hash. Hash ≠ stored hash → **signature verification fails → transaction rejected**.

The exact divergent values:
- Gateway A hash: `poseidon(tip, concat(L1_GAS, 1000, 1), concat(L2_GAS, 0, 0), concat(L1_DATA_GAS, 0, 0))`
- Peer hash: `poseidon(tip, concat(L1_GAS, 1000, 1), concat(L2_GAS, 0, 0))` [7](#0-6) [8](#0-7)

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L479-488)
```rust
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L143-175)
```rust
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash, SizeOf)]
pub struct InternalRpcTransaction {
    pub tx: InternalRpcTransactionWithoutTxHash,
    pub tx_hash: TransactionHash,
}

macro_rules! implement_ref_getters {
    ($(($member_name:ident, $member_type:ty)), *) => {
        $(pub fn $member_name(&self) -> &$member_type {
            match self {
                RpcTransaction::Declare(
                    RpcDeclareTransaction::V3(tx)
                ) => &tx.$member_name,
                RpcTransaction::DeployAccount(
                    RpcDeployAccountTransaction::V3(tx)
                ) => &tx.$member_name,
                RpcTransaction::Invoke(
                    RpcInvokeTransaction::V3(tx)
                ) => &tx.$member_name
            }
        })*
    };
}

impl RpcTransaction {
    implement_ref_getters!(
        (nonce, Nonce),
        (resource_bounds, AllResourceBounds),
        (signature, TransactionSignature),
        (tip, Tip),
        (nonce_data_availability_mode, DataAvailabilityMode),
        (fee_data_availability_mode, DataAvailabilityMode)
    );
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L368-383)
```rust
impl From<RpcDeclareTransactionV3> for DeclareTransactionV3 {
    fn from(tx: RpcDeclareTransactionV3) -> Self {
        Self {
            class_hash: tx.contract_class.calculate_class_hash(),
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            tip: tx.tip,
            signature: tx.signature,
            nonce: tx.nonce,
            compiled_class_hash: tx.compiled_class_hash,
            sender_address: tx.sender_address,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
        }
    }
```

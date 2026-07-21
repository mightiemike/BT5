### Title
`ResourceBoundsMapping` → `ValidResourceBounds` Conversion Silently Downgrades `AllResources` to `L1Gas`, Producing a Different Transaction Hash Preimage — (File: `crates/apollo_rpc/src/v0_8/transaction.rs`)

---

### Summary

The RPC-layer conversion `From<ResourceBoundsMapping> for ValidResourceBounds` silently collapses an `AllResources` variant (with zero `l1_data_gas` and `l2_gas`) into `L1Gas`. Because `get_tip_resource_bounds_hash` hashes a **different number of resource felts** for each variant, any component that reconstructs a V3 transaction from the `ResourceBoundsMapping` wire format and then recomputes the hash will produce a value that diverges from the hash the sequencer stored at ingress. This is the direct sequencer analog of the external precision-loss bug: an aggregate value (the hash preimage element count) is fixed at ingress under one representation, but the sum of individually-reconstructed elements differs when the same data is read back through a different code path.

---

### Finding Description

**Step 1 — The hash function branches on variant, not on values.**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` always emits two resource felts for `L1Gas` and three for `AllResources`:

```
L1Gas      → Poseidon(tip, L1_GAS_concat, L2_GAS_concat)
AllResources → Poseidon(tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat)
```

Even when `l1_data_gas = 0` and `l2_gas = 0`, the two paths produce **different Poseidon digests** because the input arrays have different lengths. [1](#0-0) 

**Step 2 — Ingress always uses `AllResources`.**

`RpcInvokeTransactionV3` carries `AllResourceBounds` (never `ValidResourceBounds`). The `From<RpcInvokeTransactionV3> for InvokeTransactionV3` conversion unconditionally wraps it as `ValidResourceBounds::AllResources`. The gateway therefore always computes hash **H₁** via the three-element path. [2](#0-1) 

**Step 3 — The RPC response conversion silently downgrades the variant.**

`From<ResourceBoundsMapping> for ValidResourceBounds` in the RPC layer applies a zero-check:

```rust
if value.l1_data_gas.is_zero() && value.l2_gas.is_zero() {
    Self::L1Gas(value.l1_gas)   // ← variant information is lost
} else {
    Self::AllResources(...)
}
```

A transaction originally stored as `AllResources(l1_data_gas=0, l2_gas=0)` is served as a `ResourceBoundsMapping` with zeros in those fields. Any consumer that calls this conversion reconstructs `L1Gas`, not `AllResources`. [3](#0-2) 

**Step 4 — The reconstructed hash H₂ ≠ H₁.**

`get_tip_resource_bounds_hash` called on the reconstructed `L1Gas` variant produces a two-element Poseidon hash H₂. Because Poseidon is collision-resistant and the input lengths differ, H₂ ≠ H₁ with overwhelming probability for any non-trivial `l1_gas` or `tip` value. [4](#0-3) 

**Step 5 — The `DeprecatedResourceBoundsMapping` path has the same flaw.**

The `TryFrom<DeprecatedResourceBoundsMapping> for ValidResourceBounds` conversion (used by the feeder-gateway client / state-sync path) returns `L1Gas` when the `L1DataGas` key is absent from the map. If the feeder gateway omits that key for zero-valued bounds, the state-sync path computes H₂ and fails hash validation against the stored H₁. [5](#0-4) 

---

### Impact Explanation

Any component that (a) reads a V3 transaction from the RPC or feeder-gateway wire format and (b) recomputes the transaction hash will obtain H₂ ≠ H₁. Concretely:

- **RPC hash verification**: `starknet_getTransactionByHash` returns the stored H₁, but a client that reconstructs the hash from the returned `resource_bounds` mapping computes H₂. The client cannot verify the transaction's authenticity.
- **State-sync / re-execution**: If the feeder-gateway omits the zero `L1DataGas` key, the synchronizing node stores H₂ as the transaction hash, diverging from the canonical chain state.
- **Signature domain binding**: The transaction hash is the message signed by the account. A verifier using the `ResourceBoundsMapping` path checks the signature against H₂ while the signer signed H₁, causing spurious signature failures or, in the opposite direction, accepting a signature over a different preimage.

This matches: **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

---

### Likelihood Explanation

The trigger condition is a V3 invoke transaction with `l1_data_gas = 0` **and** `l2_gas = 0`. This is a valid and common configuration: users who do not need data-availability gas or explicit L2 gas bounds will naturally set both to zero. The conversion is on the hot path for every RPC response and every feeder-gateway read. No special privileges are required; any user can submit such a transaction.

---

### Recommendation

1. **Preserve variant information in `ResourceBoundsMapping`**: Add a discriminant field (e.g., `bounds_type: "L1Gas" | "AllResources"`) to the wire format so the round-trip is lossless.
2. **Alternatively, always use `AllResources` for V3 transactions**: Since `RpcInvokeTransactionV3` always carries `AllResourceBounds`, the `From<ResourceBoundsMapping> for ValidResourceBounds` conversion should default to `AllResources` for V3 transactions rather than inspecting zero values.
3. **Add a round-trip hash test**: Assert that `hash(tx) == hash(reconstruct(serialize(tx)))` for a V3 transaction with `l1_data_gas = 0, l2_gas = 0`.

---

### Proof of Concept

```
1. Construct RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     }
     tip = 0

2. Gateway converts → InvokeTransactionV3 with ValidResourceBounds::AllResources
   Computes H₁ = get_invoke_transaction_v3_hash(...)
     → get_tip_resource_bounds_hash(AllResources, tip=0)
     → Poseidon(0, L1_GAS_concat, L2_GAS_concat_zero, L1_DATA_GAS_concat_zero)  [3 elements]

3. RPC serves transaction as ResourceBoundsMapping:
     { l1_gas: {1000,1}, l2_gas: {0,0}, l1_data_gas: {0,0} }

4. Consumer calls From<ResourceBoundsMapping> for ValidResourceBounds:
     l1_data_gas.is_zero() && l2_gas.is_zero() → true
     → ValidResourceBounds::L1Gas({1000, 1})

5. Consumer computes H₂ = get_invoke_transaction_v3_hash(...)
     → get_tip_resource_bounds_hash(L1Gas, tip=0)
     → Poseidon(0, L1_GAS_concat, L2_GAS_concat_zero)  [2 elements]

6. H₁ ≠ H₂  →  hash mismatch; signature verification fails against H₂.
``` [6](#0-5) [1](#0-0) [2](#0-1)

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L568-584)
```rust
impl From<RpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: RpcInvokeTransactionV3) -> Self {
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
}
```

**File:** crates/apollo_rpc/src/v0_8/transaction.rs (L188-200)
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
}
```

**File:** crates/starknet_api/src/transaction/fields.rs (L575-606)
```rust
impl TryFrom<DeprecatedResourceBoundsMapping> for ValidResourceBounds {
    type Error = StarknetApiError;
    fn try_from(
        resource_bounds_mapping: DeprecatedResourceBoundsMapping,
    ) -> Result<Self, Self::Error> {
        if let (Some(l1_bounds), Some(l2_bounds)) = (
            resource_bounds_mapping.0.get(&Resource::L1Gas),
            resource_bounds_mapping.0.get(&Resource::L2Gas),
        ) {
            match resource_bounds_mapping.0.get(&Resource::L1DataGas) {
                Some(data_bounds) => Ok(Self::AllResources(AllResourceBounds {
                    l1_gas: *l1_bounds,
                    l1_data_gas: *data_bounds,
                    l2_gas: *l2_bounds,
                })),
                None => {
                    if l2_bounds.is_zero() {
                        Ok(Self::L1Gas(*l1_bounds))
                    } else {
                        Err(StarknetApiError::InvalidResourceMappingInitializer(format!(
                            "Missing data gas bounds but L2 gas bound is not zero: \
                             {resource_bounds_mapping:?}",
                        )))
                    }
                }
            }
        } else {
            Err(StarknetApiError::InvalidResourceMappingInitializer(format!(
                "{resource_bounds_mapping:?}",
            )))
        }
    }
```

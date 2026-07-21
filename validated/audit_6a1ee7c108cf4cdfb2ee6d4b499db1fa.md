### Title
Protobuf `ResourceBounds` Conversion Misclassifies `AllResources` as `L1Gas` When L2 and Data Gas Are Zero, Producing a Divergent Transaction Hash — (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion uses a **value-based heuristic** — checking whether `l2_gas` and `l1_data_gas` are both zero — to decide between `ValidResourceBounds::L1Gas` (pre-0.13.3) and `ValidResourceBounds::AllResources` (post-0.13.3). A post-0.13.3 V3 transaction that legitimately sets `l2_gas = 0` and `l1_data_gas = 0` is silently misclassified as `L1Gas` on the P2P receive path. Because `get_tip_resource_bounds_hash` produces a **structurally different preimage** for the two variants, the hash recomputed from the protobuf message diverges from the hash computed and stored by the gateway, breaking the canonicalization invariant.

### Finding Description

**Root cause — protobuf conversion heuristic:** [1](#0-0) 

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← wrong for post-0.13.3 txs
} else {
    ValidResourceBounds::AllResources(...)
})
```

The same value-based heuristic appears in the RPC layer: [2](#0-1) 

**Why the two variants produce different hashes:**

`get_tip_resource_bounds_hash` branches on the enum tag, not on the numeric values: [3](#0-2) 

- `L1Gas` → preimage = `[tip, L1_gas_concat, L2_gas_concat(0)]` — **no L1 data gas element**
- `AllResources` → preimage = `[tip, L1_gas_concat, L2_gas_concat(0), L1_DATA_GAS_concat(0)]` — **extra element even when zero**

The two Poseidon hashes are therefore distinct for identical numeric field values.

**Gateway path always uses `AllResources`:**

Every `RpcTransaction` variant stores `AllResourceBounds` (not `ValidResourceBounds`), and every conversion to the internal/executable type hard-wraps it as `ValidResourceBounds::AllResources`: [4](#0-3) 

So the gateway computes and stores hash **H₂** (AllResources preimage). The storage serializer also preserves the `L1DataGas` key, so round-tripping through storage is correct: [5](#0-4) 

**P2P path recomputes hash H₁ (L1Gas preimage):**

When the same transaction arrives over P2P as a protobuf message with `l1_data_gas = Some(0)`, `unwrap_or_default()` yields a zero `ResourceBounds`, the heuristic fires, and the receiver reconstructs `ValidResourceBounds::L1Gas`. The hash recomputed from this object is **H₁ ≠ H₂**. [6](#0-5) 

### Impact Explanation

**High — Transaction conversion binds the wrong hash type, causing the P2P/sync path to compute a hash that diverges from the canonical gateway hash.**

Any node that receives the block via P2P and recomputes transaction hashes for verification will observe a mismatch for every V3 transaction whose `l2_gas` and `l1_data_gas` are both zero. This causes those transactions — and potentially the entire block — to be rejected, breaking state synchronization.

### Likelihood Explanation

**Medium.** The trigger is an unprivileged, structurally valid V3 transaction with `l2_gas = 0` and `l1_data_gas = 0`. No special permissions are required. The condition is reachable whenever a user submits a transaction that only consumes L1 gas (a common pattern for simple transfers or calls on networks where L2 gas is not yet enforced).

### Recommendation

Replace the value-based heuristic with a **presence-based** check. The protobuf `ResourceBounds` message already carries `l1_data_gas` as an `Option`; use `None` to signal pre-0.13.3 and `Some(_)` (including `Some(zero)`) to signal post-0.13.3:

```rust
// Before (wrong):
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else { ... })

// After (correct):
match value.l1_data_gas {
    None => ValidResourceBounds::L1Gas(l1_gas),   // pre-0.13.3: field absent
    Some(d) => ValidResourceBounds::AllResources(AllResourceBounds {
        l1_gas, l2_gas, l1_data_gas: d.try_into()?,
    }),
}
```

Apply the same fix to `From<ResourceBoundsMapping> for ValidResourceBounds` in `crates/apollo_rpc/src/v0_8/transaction.rs`.

### Proof of Concept

1. Submit a V3 `invoke` transaction via the gateway with `l1_gas = X`, `l2_gas = 0`, `l1_data_gas = 0`.
2. Gateway wraps bounds as `ValidResourceBounds::AllResources` → computes hash **H₂** (preimage includes the zero `L1_DATA_GAS` element) → stores `(tx, H₂)`.
3. Block is proposed and committed; the transaction is serialised into a protobuf `ResourceBounds` message with `l1_data_gas = Some(0)`.
4. A syncing node receives the block over P2P; `TryFrom<protobuf::ResourceBounds>` fires: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas`.
5. `get_tip_resource_bounds_hash` is called with `L1Gas` → preimage omits the `L1_DATA_GAS` element → hash **H₁ ≠ H₂**.
6. Hash verification fails; the syncing node rejects the transaction (and the block), halting synchronisation.

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L426-436)
```rust
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L697-714)
```rust
impl From<RpcInvokeTransactionV3> for InternalRpcInvokeTransactionV3 {
    fn from(tx: RpcInvokeTransactionV3) -> Self {
        Self {
            sender_address: tx.sender_address,
            calldata: tx.calldata,
            signature: tx.signature,
            nonce: tx.nonce,
            resource_bounds: tx.resource_bounds,
            tip: tx.tip,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            proof_facts: tx.proof_facts,
            // Note: proof field is dropped
        }
    }
}
```

**File:** crates/starknet_api/src/transaction/fields.rs (L363-367)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}
```

**File:** crates/starknet_api/src/transaction/fields.rs (L551-573)
```rust
impl Serialize for ValidResourceBounds {
    fn serialize<S>(&self, s: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        let map = match self {
            ValidResourceBounds::L1Gas(l1_gas) => BTreeMap::from([
                (Resource::L1Gas, *l1_gas),
                (Resource::L2Gas, ResourceBounds::default()),
            ]),
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => BTreeMap::from([
                (Resource::L1Gas, *l1_gas),
                (Resource::L2Gas, *l2_gas),
                (Resource::L1DataGas, *l1_data_gas),
            ]),
        };
        DeprecatedResourceBoundsMapping(map).serialize(s)
    }
}
```

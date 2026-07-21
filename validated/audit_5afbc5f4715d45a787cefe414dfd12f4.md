### Title
`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` Silently Downgrades `AllResources` to `L1Gas`, Producing a Wrong Transaction Hash on P2P Sync - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf-to-Rust conversion for `ValidResourceBounds` silently changes the variant from `AllResources` to `L1Gas` whenever `l2_gas` and `l1_data_gas` are both zero. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` element in the Poseidon preimage only for `AllResources`, the two variants produce structurally different hash inputs. A transaction originally hashed as `AllResources` (with the `L1_DATA_GAS` field in the preimage) will be re-hashed as `L1Gas` (without it) after a protobuf round-trip, yielding a different `TransactionHash`. A syncing node that receives such a transaction via P2P will compute a hash that does not match the hash committed in the block, causing it to either reject a valid block or store the transaction under the wrong hash.

### Finding Description

**Step 1 – The downgrade in the protobuf converter**

`crates/apollo_protobuf/src/converters/transaction.rs` lines 417–436:

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        ...
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // silently defaults to zero
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)                      // ← variant changed
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
}
``` [1](#0-0) 

An `AllResources` transaction whose `l2_gas` and `l1_data_gas` fields are both zero is serialised to protobuf with those fields present but zero-valued. On deserialisation the condition `l1_data_gas.is_zero() && l2_gas.is_zero()` is true, so the result is `ValidResourceBounds::L1Gas(l1_gas)` — a different enum variant.

**Step 2 – The hash preimage depends on the variant**

`crates/starknet_api/src/transaction_hash.rs` lines 188–211:

```rust
pub fn get_tip_resource_bounds_hash(
    resource_bounds: &ValidResourceBounds,
    tip: &Tip,
) -> Result<Felt, StarknetApiError> {
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],                          // ← 2 elements
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // ← 3 elements
        }
    });
    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
``` [2](#0-1) 

`AllResources` hashes `[tip, L1_GAS, L2_GAS, L1_DATA_GAS]` (4 elements).  
`L1Gas` hashes `[tip, L1_GAS, L2_GAS]` (3 elements).  
These produce different Poseidon digests even when `l2_gas = l1_data_gas = 0`.

**Step 3 – The divergent path**

1. A user submits `RpcInvokeTransactionV3 { resource_bounds: AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 } }`. The gateway stateless validator accepts this (zero l2/l1_data bounds are explicitly tested as valid).
2. The gateway converts it to `InvokeTransactionV3 { resource_bounds: ValidResourceBounds::AllResources(...) }` and computes `tx_hash = H_allresources` (preimage includes `L1_DATA_GAS`).
3. The transaction is included in a block with `tx_hash = H_allresources`.
4. A syncing peer receives the block via P2P. The `InvokeTransactionV3` is serialised to protobuf and deserialised using `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, yielding `ValidResourceBounds::L1Gas(l1_gas)`.
5. The syncing peer recomputes `tx_hash = H_l1gas` (preimage omits `L1_DATA_GAS`).
6. `H_allresources ≠ H_l1gas` → hash mismatch. [3](#0-2) [4](#0-3) 

### Impact Explanation

A syncing node recomputes a transaction hash that differs from the hash committed in the block. Depending on whether hash validation is enforced during sync, the node either rejects a valid block (liveness failure) or stores the transaction under the wrong hash (data integrity failure). Either outcome constitutes an authoritative-looking wrong value returned by RPC/state queries, matching the **High** impact: *"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."*

### Likelihood Explanation

The trigger condition — an `AllResources` transaction with zero `l2_gas` and zero `l1_data_gas` — is explicitly accepted by the gateway's stateless validator (test cases confirm `AllResourceBounds { l1_gas: NON_EMPTY, ..Default::default() }` is valid). Any user can craft such a transaction without special privileges. The TODO comment `// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2` confirms the permissive default is intentional for backward compatibility, making the window permanent until the TODO is resolved.

### Recommendation

Replace the silent `unwrap_or_default` with a strict check that preserves the original variant. If `l1_data_gas` is absent, treat the message as a legacy pre-0.13.3 transaction and return `L1Gas`. If `l1_data_gas` is present (even as zero), return `AllResources` to preserve the hash preimage:

```rust
Ok(match value.l1_data_gas {
    None => ValidResourceBounds::L1Gas(l1_gas),   // legacy: no data-gas field at all
    Some(raw) => {
        let l1_data_gas: ResourceBounds = raw.try_into()?;
        ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
    }
})
```

This preserves the invariant that the variant is determined by the presence of the `l1_data_gas` field in the wire message, not by its value, matching the hash-domain boundary in `get_tip_resource_bounds_hash`.

### Proof of Concept

```
Original AllResources transaction:
  resource_bounds = AllResources { l1_gas: (amount=5, price=100), l2_gas: (0,0), l1_data_gas: (0,0) }
  tip = 0

get_tip_resource_bounds_hash (AllResources path):
  preimage = [0, concat(L1_GAS, 5, 100), concat(L2_GAS, 0, 0), concat(L1_DATA, 0, 0)]
  → H_allresources = poseidon([0, A, B, C])

After protobuf round-trip (l1_data_gas.is_zero() && l2_gas.is_zero() → L1Gas):
  resource_bounds = L1Gas { l1_gas: (amount=5, price=100) }

get_tip_resource_bounds_hash (L1Gas path):
  preimage = [0, concat(L1_GAS, 5, 100), concat(L2_GAS, 0, 0)]
  → H_l1gas = poseidon([0, A, B])

H_allresources ≠ H_l1gas  ← hash mismatch on syncing node
``` [5](#0-4) [6](#0-5)

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

**File:** crates/starknet_api/src/transaction/fields.rs (L363-367)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}
```

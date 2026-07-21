### Title
`AllResources` → `L1Gas` Downgrade in Protobuf Deserialization Produces Wrong Transaction Hash for Syncing Nodes - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` silently downgrades an `AllResources` transaction (with `l2_gas = 0` and `l1_data_gas = 0`) to the `L1Gas` variant. Because `get_tip_resource_bounds_hash` produces structurally different Poseidon hash preimages for the two variants (3 resource felts vs. 2), a transaction accepted by the gateway with hash `H_all` is re-hashed to a different value `H_l1` by every node that receives it over the P2P sync path. Syncing nodes store, serve, and commit the wrong transaction hash.

### Finding Description

**Step 1 – Invariant.** `get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` produces two structurally distinct Poseidon preimages:

- `ValidResourceBounds::L1Gas` → chains `[tip, L1_GAS_concat, L2_GAS_concat]` (2 resource felts)
- `ValidResourceBounds::AllResources` → chains `[tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat]` (3 resource felts) [1](#0-0) 

**Step 2 – Serializer always emits `l1_data_gas = Some(…)`.** `From<ValidResourceBounds> for protobuf::ResourceBounds` always sets `l1_data_gas` to `Some(...)`, even when the value is zero: [2](#0-1) 

**Step 3 – Deserializer silently drops `l1_data_gas` and downgrades the variant.** `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` uses `unwrap_or_default()` for `l1_data_gas`, then applies a zero-check to decide the variant:

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [3](#0-2) 

**Step 4 – The collision.** A transaction submitted via RPC with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is valid (gateway accepts it because `max_possible_fee > 0` when `l1_gas` is non-zero): [4](#0-3) 

The gateway computes the hash as `AllResources` (3-felt preimage) → `H_all`. When the block is synced over P2P, the serializer emits `l1_data_gas = Some(zero)`. The deserializer sees `l1_data_gas.is_zero() && l2_gas.is_zero()` → `L1Gas`. The syncing node recomputes the hash as `L1Gas` (2-felt preimage) → `H_l1 ≠ H_all`.

The `ValidResourceBounds` enum definition confirms the two variants are semantically distinct: [5](#0-4) 

The consensus-path protobuf converter for `RpcTransaction` uses `AllResourceBounds` directly and requires `l1_data_gas` to be present (errors if missing), so it is not affected. Only the block-sync path (`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`) is vulnerable: [6](#0-5) 

### Impact Explanation

Syncing nodes store `H_l1` as the canonical transaction hash while the proposing node (and all RPC clients) know the transaction by `H_all`. Consequences:

- **Wrong receipt / event hash**: RPC calls to `starknet_getTransactionReceipt` on a syncing node return a receipt keyed by `H_l1`; queries for `H_all` return "not found."
- **Wrong state commitment**: if the transaction hash is an input to the block hash or state diff, the syncing node computes a divergent commitment.
- **Wrong fee estimation / tracing**: `starknet_estimateFee` and `starknet_traceTransaction` on the syncing node operate on the wrong hash, returning authoritative-looking wrong values.

This matches the allowed impact: *"Wrong state, receipt, event … storage value … from blockifier/syscall/execution logic for accepted input"* and *"RPC execution … returns an authoritative-looking wrong value."*

### Likelihood Explanation

The trigger requires a transaction with `AllResourceBounds { l1_gas > 0, l2_gas = 0, l1_data_gas = 0 }`. The gateway explicitly allows this (only the total fee must be non-zero). Any user who sets only `l1_gas` and leaves the other two bounds at zero (a common pattern for pre-0.13.3-style usage submitted through the new API) triggers the bug on every syncing peer. No privileged access is required.

### Recommendation

In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, preserve the `AllResources` variant whenever the wire message was produced by a post-0.13.2 serializer (i.e., when `l1_data_gas` is `Some`, even if zero). The downgrade to `L1Gas` should only occur when `l1_data_gas` is `None` (absent from the wire) **and** `l2_gas` is zero:

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let Some(l1_gas) = value.l1_gas else { return Err(missing("...")); };
        let Some(l2_gas) = value.l2_gas else { return Err(missing("...")); };
        let l1_gas: ResourceBounds = l1_gas.try_into()?;
        let l2_gas: ResourceBounds = l2_gas.try_into()?;

        match value.l1_data_gas {
            // l1_data_gas absent → legacy 0.13.2 transaction; use L1Gas only if l2_gas is also zero
            None if l2_gas.is_zero() => Ok(ValidResourceBounds::L1Gas(l1_gas)),
            None => Err(missing("ResourceBounds::l1_data_gas")),
            // l1_data_gas present (even if zero) → always AllResources to preserve hash domain
            Some(l1_data_gas) => {
                let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
                Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))
            }
        }
    }
}
```

### Proof of Concept

1. Submit an invoke transaction via RPC with `resource_bounds = { L1_GAS: { max_amount: 1000, max_price_per_unit: 1 }, L2_GAS: { max_amount: 0, max_price_per_unit: 0 }, L1_DATA_GAS: { max_amount: 0, max_price_per_unit: 0 } }`. The gateway accepts it and computes `H_all` using the 3-felt `AllResources` Poseidon preimage.

2. Let the proposing node include it in a block. Observe `H_all` in the block header / receipt.

3. Start a syncing node. It fetches the block over P2P. The protobuf message carries `l1_data_gas = Some(zero)`. The deserializer evaluates `l1_data_gas.is_zero() && l2_gas.is_zero()` → `true` → `ValidResourceBounds::L1Gas`. The syncing node recomputes the hash using the 2-felt `L1Gas` preimage → `H_l1`.

4. Query `starknet_getTransactionReceipt(H_all)` on the syncing node → "transaction not found." Query `starknet_getTransactionReceipt(H_l1)` → returns a receipt, demonstrating the hash divergence.

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L197-210)
```rust
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L64-69)
```rust
        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
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

### Title
`ValidResourceBounds` Protobuf Round-Trip Silently Downgrades `AllResources` to `L1Gas` When L2/L1DataGas Are Zero, Producing a Different Transaction Hash Across Nodes — (`crates/apollo_protobuf/src/converters/transaction.rs` + `crates/starknet_api/src/transaction_hash.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` silently converts an `AllResources` variant (with zero `l2_gas` and `l1_data_gas`) into the `L1Gas` variant. Because `get_tip_resource_bounds_hash` hashes a different number of resource-bound elements depending on the variant, the same logical transaction produces two distinct Poseidon hashes: one at the submitting gateway (computed over `AllResources`, 4 elements) and a different one at any peer that deserializes the P2P protobuf message (computed over `L1Gas`, 3 elements). This is a direct sequencer-native analog of the "skip-when-zero" state-update omission in the original report.

---

### Finding Description

**Step 1 — Hash computation is variant-sensitive.**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` builds the fee-fields hash differently for the two `ValidResourceBounds` variants:

```rust
// L1 and L2 gas bounds always exist.
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];

// For new V3 txs, need to also hash the data gas bounds.
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // ← skipped
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
```

- `AllResources` → Poseidon over **4** elements: `[tip, l1_gas, l2_gas, l1_data_gas]`
- `L1Gas` → Poseidon over **3** elements: `[tip, l1_gas, l2_gas=0]`

Even when `l2_gas = 0` and `l1_data_gas = 0`, the two hashes are numerically different because Poseidon over `[a, b, c, 0]` ≠ Poseidon over `[a, b, c]`. [1](#0-0) 

**Step 2 — Protobuf deserialization silently changes the variant.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` applies the following rule:

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant changes here
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

Any `AllResources` message whose `l2_gas` and `l1_data_gas` fields are both zero is silently re-typed as `L1Gas` on the receiving side. [2](#0-1) 

**Step 3 — The gateway always produces `AllResources`.**

`InternalRpcInvokeTransactionV3` (the internal form of an RPC-submitted invoke) always wraps its `AllResourceBounds` field as `ValidResourceBounds::AllResources(...)` when implementing `InvokeTransactionV3Trait::resource_bounds()`:

```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)   // always AllResources
    }
    ...
}
```

So the hash computed at the gateway always uses the 4-element path. [3](#0-2) 

**Step 4 — The P2P path uses the lossy converter.**

When the transaction is serialized to protobuf for P2P propagation (`From<InvokeTransactionV3> for protobuf::InvokeV3`), the `l1_data_gas` field is written as zero. On the receiving peer, `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` fires and downgrades the variant to `L1Gas`. The peer then recomputes the hash using the 3-element path, obtaining a value that differs from the hash stored in the block/receipt on the originating node. [4](#0-3) 

---

### Impact Explanation

The transaction hash is the canonical identifier used in receipts, events, L1 messages, and state commitments. When the originating sequencer node commits a block containing a transaction with hash H_all, but every peer that received the transaction via P2P recomputes H_l1 ≠ H_all, the following divergences occur:

- **Receipt mismatch**: the receipt stored by the originating node carries H_all; peers that re-derive the hash from the deserialized transaction body produce H_l1.
- **RPC divergence**: `starknet_getTransactionReceipt` returns different hashes depending on which node is queried, constituting an authoritative-looking wrong value.
- **Consensus / state-root divergence**: if the transaction hash feeds into the block hash or event commitment (as it does in Starknet's block hash construction), nodes will disagree on the block hash, breaking consensus.

This matches the allowed impact: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value** and **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload**.

---

### Likelihood Explanation

Any user who submits an `InvokeTransactionV3` with `l2_gas = 0` and `l1_data_gas = 0` (a valid configuration, e.g., a legacy-style V3 transaction that sets only L1 gas bounds) triggers the divergence automatically. No special privilege is required; the condition is reachable through the normal public RPC endpoint. The `TODO` comment in the converter acknowledges the backward-compatibility intent but does not guard against the hash-domain split.

---

### Recommendation

1. **Fix the protobuf converter**: do not downgrade `AllResources` to `L1Gas` based on zero values. Instead, preserve the original variant. The `L1Gas` variant should only be produced when the wire message was explicitly sent without `l1_data_gas` (i.e., `value.l1_data_gas.is_none()`), not when it is present but zero:

```rust
let l1_data_gas_opt = value.l1_data_gas;
Ok(match l1_data_gas_opt {
    None => ValidResourceBounds::L1Gas(l1_gas),   // truly absent → legacy
    Some(raw) => {
        let l1_data_gas: ResourceBounds = raw.try_into()?;
        ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
    }
})
```

2. **Alternatively, canonicalize the hash**: make `get_tip_resource_bounds_hash` always include the `l1_data_gas` element (as zero when absent), so both variants produce the same hash for the same numeric values. This matches the Cairo OS `hash_fee_fields` which always asserts `n_resource_bounds = 3` and hashes all three. [5](#0-4) 

---

### Proof of Concept

```
1. Submit via RPC:
   InvokeTransactionV3 {
       resource_bounds: AllResourceBounds {
           l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
           l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
           l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
       },
       ...
   }

2. Gateway computes:
   tip_resource_hash_all = Poseidon([tip, pack(L1_GAS,1000,1), pack(L2_GAS,0,0), pack(L1_DATA_GAS,0,0)])
   tx_hash_all = Poseidon([INVOKE, version, sender, tip_resource_hash_all, ...])

3. Transaction is serialized to protobuf::InvokeV3 with l1_data_gas = ResourceLimits{0,0}.

4. Peer deserializes:
   TryFrom<protobuf::ResourceBounds>:
     l1_data_gas.is_zero() && l2_gas.is_zero()  →  ValidResourceBounds::L1Gas(l1_gas)

5. Peer computes:
   tip_resource_hash_l1 = Poseidon([tip, pack(L1_GAS,1000,1), pack(L2_GAS,0,0)])
   tx_hash_l1 = Poseidon([INVOKE, version, sender, tip_resource_hash_l1, ...])

6. tx_hash_all ≠ tx_hash_l1  →  hash domain split between originating node and all peers.
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L119-144)
```text
    static_assert L1_GAS_INDEX == 0;
    static_assert L2_GAS_INDEX == 1;
    static_assert L1_DATA_GAS_INDEX == 2;

    with_attr error_message("Invalid number of resource bounds: {n_resource_bounds}.") {
        assert n_resource_bounds = 3;
    }

    // L1 gas.
    let l1_gas_bounds = resource_bounds[L1_GAS_INDEX];
    assert l1_gas_bounds.resource = L1_GAS;
    assert data_to_hash[1] = pack_resource_bounds(l1_gas_bounds);

    // L2 gas.
    let l2_gas_bounds = resource_bounds[L2_GAS_INDEX];
    assert l2_gas_bounds.resource = L2_GAS;
    assert data_to_hash[2] = pack_resource_bounds(l2_gas_bounds);

    // L1 data gas.
    let l1_data_gas_bounds = resource_bounds[L1_DATA_GAS_INDEX];
    assert l1_data_gas_bounds.resource = L1_DATA_GAS;
    assert data_to_hash[3] = pack_resource_bounds(l1_data_gas_bounds);

    let (hash) = poseidon_hash_many(n=n_resource_bounds + 1, elements=data_to_hash);
    return hash;
}
```

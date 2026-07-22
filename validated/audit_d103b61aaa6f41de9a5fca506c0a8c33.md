### Title
Protobuf `ValidResourceBounds` Round-Trip Silently Converts `AllResources` to `L1Gas` When L2/L1DataGas Are Zero, Producing a Divergent Transaction Hash in the P2P Sync Path - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` deserialization uses a value-based heuristic: if both `l2_gas` and `l1_data_gas` are zero, it silently produces `ValidResourceBounds::L1Gas` instead of `ValidResourceBounds::AllResources`. However, `get_tip_resource_bounds_hash` computes structurally different hash preimages for these two variants — `AllResources` always appends an L1DataGas element, `L1Gas` never does. A valid RPC transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is hashed as `AllResources` (H1) on the originating node, but after protobuf round-trip in the P2P sync path the same bytes are deserialized as `L1Gas(X)` and rehashed as H2 ≠ H1, breaking the invariant that a transaction hash is canonical across all nodes.

### Finding Description

**Hash domain boundary — `get_tip_resource_bounds_hash`**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` branches on the enum variant, not on the numeric values:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 elements total
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 elements total
    }
});
``` [1](#0-0) 

So `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` hashes as `poseidon(tip, concat(L1GAS,X), concat(L2GAS,0), concat(L1DATAGAAS,0))` while `L1Gas(X)` hashes as `poseidon(tip, concat(L1GAS,X), concat(L2GAS,0))`. These are distinct field elements.

**Lossy protobuf deserialization**

The forward serializer always emits all three fields, even for `L1Gas`:

```rust
ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
    l1_gas: Some(l1_gas.into()),
    l2_gas: Some(value.get_l2_bounds().into()),          // zero
    l1_data_gas: Some(ResourceBounds::default().into()), // zero
},
``` [2](#0-1) 

The reverse deserializer then applies a value-based heuristic:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [3](#0-2) 

This means `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` → protobuf → `L1Gas(X)`. The variant changes, and therefore the hash changes.

**Contrast with JSON serialization**

The JSON serializer for `ValidResourceBounds::L1Gas` emits only two keys (`L1GAS`, `L2GAS`), while `AllResources` emits three (`L1GAS`, `L2GAS`, `L1DATAGAAS`). The JSON deserializer branches on key presence, not value:

```rust
match resource_bounds_mapping.0.get(&Resource::L1DataGas) {
    Some(data_bounds) => Ok(Self::AllResources(...)),  // key present → AllResources
    None => { if l2_bounds.is_zero() { Ok(Self::L1Gas(...)) } ... }
}
``` [4](#0-3) 

JSON round-trips are lossless. Protobuf round-trips are not.

**Attacker-controlled trigger**

The gateway's stateless validator explicitly accepts transactions with only `l1_gas` non-zero:

```rust
#[case::valid_l1_gas(
    StatelessTransactionValidatorConfig { validate_resource_bounds: true, ... },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l1_gas: NON_EMPTY_RESOURCE_BOUNDS,
            ..Default::default()   // l2_gas = 0, l1_data_gas = 0
        }, ...
    }
)]
``` [5](#0-4) 

Any unprivileged user can submit such a transaction via the public RPC endpoint.

**Hash validation in the sync path**

`validate_transaction_hash` recomputes the hash from the deserialized `Transaction` struct and compares it against the stored expected hash:

```rust
possible_hashes.push(get_transaction_hash(transaction, chain_id, transaction_options)?);
Ok(possible_hashes.contains(&expected_hash))
``` [6](#0-5) 

If the deserialized `ValidResourceBounds` variant differs from the original, the recomputed hash H2 will not match the stored H1, and validation returns `false`.

### Impact Explanation

A syncing node that receives a block containing a transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` via the P2P protobuf sync protocol will deserialize the transaction's resource bounds as `ValidResourceBounds::L1Gas(X)`, recompute a hash H2 that differs from the canonical H1 stored in the block, and reject the block as having an invalid transaction hash. This constitutes a wrong hash/wrong state result for accepted input, matching the "Critical. Wrong state … or revert result from blockifier/syscall/execution logic for accepted input" and "High. RPC execution … returns an authoritative-looking wrong value" impact categories. Additionally, if the wrong hash H2 is used as the `tx_hash` passed to the account's `__validate__` entry point, the ECDSA check `is_valid_signature(H2, sig)` fails (the user signed H1), causing the transaction to revert on any node that processes it through the protobuf path while it succeeds on the originating node — a consensus-observable divergence.

### Likelihood Explanation

The trigger requires only a standard RPC `starknet_addInvokeTransaction` call with `l2_gas = 0` and `l1_data_gas = 0`, which the gateway explicitly accepts. No privileged access, special keys, or malformed bytes are needed. The divergence manifests automatically whenever such a transaction is included in a block that is subsequently synced via P2P.

### Recommendation

Fix the protobuf deserializer to preserve the `AllResources` variant whenever all three resource-bound fields are present in the wire message, regardless of their numeric values. The discriminant should be structural (field presence), not semantic (field value), consistent with the JSON deserializer:

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let l1_gas = ...;
        let l2_gas = ...;
        // If l1_data_gas is present in the message, always use AllResources.
        if let Some(l1_data_gas_proto) = value.l1_data_gas {
            let l1_data_gas: ResourceBounds = l1_data_gas_proto.try_into()?;
            return Ok(ValidResourceBounds::AllResources(
                AllResourceBounds { l1_gas, l2_gas, l1_data_gas }
            ));
        }
        // Only fall back to L1Gas if l1_data_gas is absent (legacy 0.13.2 messages).
        Ok(ValidResourceBounds::L1Gas(l1_gas))
    }
}
```

Correspondingly, the forward serializer for `ValidResourceBounds::L1Gas` should omit the `l1_data_gas` field entirely (set it to `None`) so that legacy and new messages remain distinguishable at the wire level.

### Proof of Concept

1. Submit via RPC:
   ```json
   { "type": "INVOKE", "version": "0x3",
     "resource_bounds": {
       "l1_gas":      { "max_amount": "0x100", "max_price_per_unit": "0x1" },
       "l2_gas":      { "max_amount": "0x0",   "max_price_per_unit": "0x0" },
       "l1_data_gas": { "max_amount": "0x0",   "max_price_per_unit": "0x0" }
     }, ... }
   ```
2. Gateway accepts the transaction; `convert_rpc_tx_to_internal` calls `tx_without_hash.calculate_transaction_hash` with `ValidResourceBounds::AllResources { l2_gas: 0, l1_data_gas: 0 }`, producing H1 (3-element resource hash). [7](#0-6) 
3. Transaction is included in a block; H1 is stored as the canonical hash.
4. A syncing peer requests the block via P2P; the transaction is serialized as `protobuf::InvokeV3` with `l1_data_gas = { max_amount: 0, max_price_per_unit: 0 }`.
5. The peer deserializes: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas(X)`. [8](#0-7) 
6. `get_tip_resource_bounds_hash` is called with `L1Gas(X)`, producing H2 (2-element resource hash, no L1DataGas term). [9](#0-8) 
7. `validate_transaction_hash` compares H2 against stored H1; H2 ≠ H1 → block rejected. [6](#0-5)

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L183-184)
```rust
    possible_hashes.push(get_transaction_hash(transaction, chain_id, transaction_options)?);
    Ok(possible_hashes.contains(&expected_hash))
```

**File:** crates/starknet_api/src/transaction_hash.rs (L203-210)
```rust
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L431-435)
```rust
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L474-478)
```rust
            ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
                l1_gas: Some(l1_gas.into()),
                l2_gas: Some(value.get_l2_bounds().into()),
                l1_data_gas: Some(ResourceBounds::default().into()),
            },
```

**File:** crates/starknet_api/src/transaction/fields.rs (L584-599)
```rust
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
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L70-82)
```rust
#[case::valid_l1_gas(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l1_gas: NON_EMPTY_RESOURCE_BOUNDS,
            ..Default::default()
        },
        ..Default::default()
    }
)]
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

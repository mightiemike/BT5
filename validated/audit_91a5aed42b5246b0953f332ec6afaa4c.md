### Title
Protobuf `ValidResourceBounds` round-trip silently collapses `AllResources{l2_gas=0, l1_data_gas=0}` to `L1Gas`, producing a divergent transaction hash on syncing nodes - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf serialization/deserialization of `ValidResourceBounds` is not injective: both `ValidResourceBounds::L1Gas(X)` and `ValidResourceBounds::AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` serialize to the identical protobuf wire bytes, but the two variants produce **different transaction hashes** via `get_tip_resource_bounds_hash`. A user can submit a valid V3 transaction through the RPC gateway using `AllResourceBounds` with zero `l2_gas` and `l1_data_gas`; the sequencer computes and stores hash H1 (4-element Poseidon preimage). When a syncing node receives the block over P2P, the protobuf deserializer reclassifies the bounds as `L1Gas` and recomputes hash H2 (3-element Poseidon preimage). H1 ≠ H2, so the syncing node stores and serves the wrong transaction hash.

---

### Finding Description

**Step 1 – Serialization loses the variant tag.**

`From<ValidResourceBounds> for protobuf::ResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` serializes `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` as:

```
{ l1_gas: Some(X), l2_gas: Some(0), l1_data_gas: Some(0) }
```

It serializes `L1Gas(X)` as:

```
{ l1_gas: Some(X), l2_gas: Some(0), l1_data_gas: Some(0) }   // default zero for l1_data_gas
```

Both produce **identical wire bytes**. [1](#0-0) 

**Step 2 – Deserialization always picks `L1Gas` when both gas fields are zero.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` applies:

```rust
let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // zero
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)                      // ← always chosen
} else { ... })
```

So `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is permanently reclassified as `L1Gas(X)` after one protobuf round-trip. [2](#0-1) 

**Step 3 – The two variants produce different hash preimages.**

`get_tip_resource_bounds_hash` branches on the variant:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 3-element preimage
    ValidResourceBounds::AllResources(all) =>
        vec![get_concat_resource(&all.l1_data_gas, L1_DATA_GAS)?],    // 4-element preimage
});
```

`get_concat_resource` packs the 7-byte ASCII resource name `L1_DATA` into the felt even when `max_amount = 0` and `max_price_per_unit = 0`, producing a **non-zero** felt `0x00 || b"L1_DATA" || 0x00..00`. Poseidon over 3 vs 4 elements yields different digests. [3](#0-2) 

**Step 4 – The gateway always hashes as `AllResources`.**

`InternalRpcInvokeTransactionV3` stores `resource_bounds: AllResourceBounds` and its `InvokeTransactionV3Trait` impl unconditionally wraps it:

```rust
fn resource_bounds(&self) -> ValidResourceBounds {
    ValidResourceBounds::AllResources(self.resource_bounds)
}
```

So the sequencer always computes H1 (4-element preimage) for any RPC-submitted V3 transaction, regardless of whether `l2_gas` and `l1_data_gas` are zero. [4](#0-3) 

**Step 5 – The syncing node recomputes H2 (3-element preimage).**

After protobuf deserialization the syncing node holds `InvokeTransactionV3` with `resource_bounds: ValidResourceBounds::L1Gas(X)`. Its `InvokeTransactionV3Trait` impl returns `self.resource_bounds` directly, so `get_invoke_transaction_v3_hash` calls `get_tip_resource_bounds_hash` with `L1Gas`, omitting the `L1_DATA_GAS` element. The resulting hash H2 ≠ H1. [5](#0-4) 

---

### Impact Explanation

A syncing node stores and serves every V3 transaction whose `l2_gas = 0` and `l1_data_gas = 0` under the wrong hash H2. Concretely:

- `starknet_getTransactionByHash(H1)` returns "not found" on syncing nodes even though the transaction is in the canonical chain.
- `starknet_getTransactionByHash(H2)` returns an authoritative-looking response with a hash field that does not match the on-chain record.
- Any RPC method that re-derives the transaction hash from stored fields (fee estimation, tracing, simulation) returns H2 instead of H1, a wrong authoritative value.

This matches: **High – RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

---

### Likelihood Explanation

The trigger requires only a standard V3 RPC submission with `AllResourceBounds { l1_gas: nonzero, l2_gas: 0, l1_data_gas: 0 }`. The gateway's stateless validator explicitly accepts this pattern (test case `valid_l1_gas` passes with exactly these bounds). [6](#0-5) 

No special privilege, no malformed bytes, and no adversarial peer is required. Any ordinary user submitting a pre-0.13.3-style V3 transaction (L1-gas-only bounds) triggers the divergence automatically on every syncing node.

---

### Recommendation

The protobuf encoding must preserve the `ValidResourceBounds` variant. Two options:

1. **Add a discriminant field** to `protobuf::ResourceBounds` (e.g., `bool all_resources`) so the deserializer can reconstruct the correct variant without inspecting field values.
2. **Normalize at the gateway**: reject or upgrade `AllResourceBounds { l2_gas: 0, l1_data_gas: 0 }` to `L1Gas` before computing the hash, so the sequencer and syncing nodes always agree on the variant. This requires a coordinated protocol change.

The `TODO(Shahak)` comment at line 427 acknowledges the `unwrap_or_default` is temporary; the fix should also address the classification logic at line 431. [7](#0-6) 

---

### Proof of Concept

```
1. Submit via starknet_addInvokeTransaction (RPC v0.8):
   {
     "type": "INVOKE",
     "version": "0x3",
     "resource_bounds": {
       "l1_gas":      { "max_amount": "0x100", "max_price_per_unit": "0x1" },
       "l2_gas":      { "max_amount": "0x0",   "max_price_per_unit": "0x0" },
       "l1_data_gas": { "max_amount": "0x0",   "max_price_per_unit": "0x0" }
     },
     ... (valid nonce, signature, calldata)
   }

2. Gateway accepts the transaction.
   InternalRpcInvokeTransactionV3::resource_bounds() → AllResources{l1_gas=X, l2_gas=0, l1_data_gas=0}
   get_tip_resource_bounds_hash → poseidon(tip, pack(L1_GAS,X), pack(L2_GAS,0), pack(L1_DATA,0))
   → H1 stored in block.

3. Syncing node receives block over P2P protobuf.
   TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
     l1_data_gas = Some(0).unwrap_or_default() = 0
     l2_gas = 0
     → ValidResourceBounds::L1Gas(X)
   get_tip_resource_bounds_hash → poseidon(tip, pack(L1_GAS,X), pack(L2_GAS,0))
   → H2 ≠ H1 stored on syncing node.

4. starknet_getTransactionByHash(H1) on syncing node → "Transaction not found"
   starknet_getTransactionByHash(H2) on syncing node → returns tx with wrong hash field H2
```

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L417-437)
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

**File:** crates/starknet_api/src/transaction_hash.rs (L188-226)
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

// Receives resource_bounds and resource_name and returns:
// [0 | resource_name (56 bit) | max_amount (64 bit) | max_price_per_unit (128 bit)].
// An implementation of the SNIP: https://github.com/EvyatarO/SNIPs/blob/snip-8/SNIPS/snip-8.md.
fn get_concat_resource(
    resource_bounds: &ResourceBounds,
    resource_name: &ResourceName,
) -> Result<Felt, StarknetApiError> {
    let max_amount = resource_bounds.max_amount.0.to_be_bytes();
    let max_price = resource_bounds.max_price_per_unit.0.to_be_bytes();
    let concat_bytes =
        [[0_u8].as_slice(), resource_name.as_slice(), max_amount.as_slice(), max_price.as_slice()]
            .concat();
    Ok(Felt::from_bytes_be(&concat_bytes.try_into().expect("Expect 32 bytes")))
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L370-405)
```rust
pub(crate) fn get_invoke_transaction_v3_hash<T: InvokeTransactionV3Trait>(
    transaction: &T,
    chain_id: &ChainId,
    transaction_version: &TransactionVersion,
) -> Result<TransactionHash, StarknetApiError> {
    let tip_resource_bounds_hash =
        get_tip_resource_bounds_hash(&transaction.resource_bounds(), transaction.tip())?;
    let paymaster_data_hash =
        HashChain::new().chain_iter(transaction.paymaster_data().0.iter()).get_poseidon_hash();
    let data_availability_mode = concat_data_availability_mode(
        transaction.nonce_data_availability_mode(),
        transaction.fee_data_availability_mode(),
    );
    let account_deployment_data_hash = HashChain::new()
        .chain_iter(transaction.account_deployment_data().0.iter())
        .get_poseidon_hash();
    let calldata_hash =
        HashChain::new().chain_iter(transaction.calldata().0.iter()).get_poseidon_hash();
    let mut hash_chain = HashChain::new()
        .chain(&INVOKE)
        .chain(&transaction_version.0)
        .chain(transaction.sender_address().0.key())
        .chain(&tip_resource_bounds_hash)
        .chain(&paymaster_data_hash)
        .chain(&Felt::try_from(chain_id)?)
        .chain(&transaction.nonce().0)
        .chain(&data_availability_mode)
        .chain(&account_deployment_data_hash)
        .chain(&calldata_hash);
    if !transaction.proof_facts().0.is_empty() {
        let proof_facts_hash =
            HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
        hash_chain = hash_chain.chain(&proof_facts_hash);
    }
    Ok(TransactionHash(hash_chain.get_poseidon_hash()))
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L69-82)
```rust
#[rstest]
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

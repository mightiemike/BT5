### Title
P2P Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas` When `l2_gas` and `l1_data_gas` Are Zero, Producing a Different Transaction Hash Than the One Computed at Submission Time - (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a value-based heuristic to select the enum variant: if both `l2_gas` and `l1_data_gas` are zero it emits `ValidResourceBounds::L1Gas`, otherwise `ValidResourceBounds::AllResources`. The gateway and internal RPC path always bind `AllResources` (the `RpcInvokeTransactionV3` and `InternalRpcInvokeTransactionV3` structs carry `AllResourceBounds`, never `ValidResourceBounds`). The transaction hash function `get_tip_resource_bounds_hash` hashes a **different number of resource felts** depending on the variant: 3 felts for `L1Gas` (tip + l1_gas + l2_gas) versus 4 felts for `AllResources` (tip + l1_gas + l2_gas + l1_data_gas). A V3 invoke transaction submitted with `AllResources` where `l2_gas = 0` and `l1_data_gas = 0` receives hash H₁ (4-element Poseidon input) at the gateway. When the same transaction is received over P2P and deserialized from protobuf, the converter silently produces `L1Gas`, and the hash recomputed from it is H₂ (3-element Poseidon input). H₁ ≠ H₂. Any component that recomputes or validates the transaction hash from the P2P-received object will diverge from the canonical hash.

---

### Finding Description

**Root cause — protobuf converter, value-based variant selection:**

```rust
// crates/apollo_protobuf/src/converters/transaction.rs  lines 417-436
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)          // ← variant chosen by value, not by wire type
        } else {
            ValidResourceBounds::AllResources(...)
        })
    }
}
``` [1](#0-0) 

**Hash function — different preimage length per variant:**

```rust
// crates/starknet_api/src/transaction_hash.rs  lines 188-211
pub fn get_tip_resource_bounds_hash(resource_bounds: &ValidResourceBounds, tip: &Tip) -> ... {
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,   // zero for L1Gas variant
    ];
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],             // ← l1_data_gas EXCLUDED
        ValidResourceBounds::AllResources(all) =>
            vec![get_concat_resource(&all.l1_data_gas, L1_DATA_GAS)?],  // ← l1_data_gas INCLUDED
    });
    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
``` [2](#0-1) 

**Gateway / internal path always uses `AllResources`:**

`RpcInvokeTransactionV3.resource_bounds` is typed `AllResourceBounds`, and the `InvokeTransactionV3Trait` impl for `InternalRpcInvokeTransactionV3` always wraps it in `ValidResourceBounds::AllResources(self.resource_bounds)`. [3](#0-2) 

So for a transaction with `l2_gas = 0, l1_data_gas = 0`:

| Path | Variant produced | Poseidon input length | Hash |
|---|---|---|---|
| Gateway / RPC submission | `AllResources` | 4 felts (tip + L1 + L2 + L1Data) | H₁ |
| P2P protobuf deserialization | `L1Gas` | 3 felts (tip + L1 + L2) | H₂ ≠ H₁ |

The divergence is exactly one zero-valued felt (`l1_data_gas_packed = 0`) appended to the Poseidon absorb sequence, which changes the output.

**Trigger condition:** Any V3 invoke transaction submitted with `AllResources` where both `l2_gas.max_amount = 0, l2_gas.max_price_per_unit = 0` and `l1_data_gas.max_amount = 0, l1_data_gas.max_price_per_unit = 0`. The gateway stateless validator explicitly accepts such transactions (the `valid_l1_gas` test case passes with only `l1_gas` non-zero). [4](#0-3) 

The consensus test file even documents the exact failure mode:

```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
``` [5](#0-4) 

---

### Impact Explanation

**High. Transaction conversion or signature/hash logic binds the wrong hash.**

Any node that receives the transaction over P2P and recomputes its hash (e.g., during `validate_transaction_hash`, block hash construction, or receipt generation) will produce H₂ instead of H₁. This causes:

1. **P2P sync nodes reject valid transactions** — `validate_transaction_hash` compares the stored hash against the recomputed hash; the mismatch causes the transaction to be treated as invalid.
2. **Wrong receipt / event hash** — if the node accepts the transaction without re-validating the hash, it stores it under H₂, diverging from the canonical chain state.
3. **Block hash divergence** — the block hash is derived from transaction hashes; a node that recomputes H₂ for a transaction whose canonical hash is H₁ will compute a different block hash, breaking consensus. [6](#0-5) 

---

### Likelihood Explanation

**Medium.** The trigger is a valid, well-formed V3 transaction with zero L2 and L1-data gas bounds. This is a normal configuration for transactions that only consume L1 gas (e.g., simple transfers on pre-0.13.3 semantics). No special privileges or malformed bytes are required. The vulnerability is latent in every P2P sync round-trip for such transactions.

---

### Recommendation

Replace the value-based variant heuristic in the protobuf converter with an explicit wire-type discriminator. The protobuf `ResourceBounds` message should carry a boolean or enum field indicating whether the sender intended `L1Gas` or `AllResources`. Until the wire format is updated, the converter should **always** produce `AllResources` when deserializing a transaction received from a peer that is known to be running a post-0.13.3 sequencer, and should only fall back to `L1Gas` for explicitly versioned legacy messages. Concretely:

```rust
// Always produce AllResources for new transactions; only use L1Gas for
// explicitly legacy (0.13.2) messages where l1_data_gas is absent from the wire.
Ok(if value.l1_data_gas.is_none() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [7](#0-6) 

---

### Proof of Concept

```
1. Craft a V3 invoke transaction T with:
     resource_bounds = AllResourceBounds {
         l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     }

2. Submit T to the gateway.
   Gateway computes H₁ = Poseidon(INVOKE, 3, sender, Poseidon(tip, L1_packed, L2_packed(0), L1DATA_packed(0)), ..., nonce, ...)
   H₁ is stored in the mempool and propagated.

3. A P2P sync peer receives T serialized as protobuf::ResourceBounds with
   l1_gas = {1000, 1}, l2_gas = {0, 0}, l1_data_gas = {0, 0}.

4. TryFrom<protobuf::ResourceBounds> for ValidResourceBounds fires:
   l1_data_gas.is_zero() && l2_gas.is_zero() == true
   → produces ValidResourceBounds::L1Gas({1000, 1})

5. Peer recomputes H₂ = Poseidon(INVOKE, 3, sender, Poseidon(tip, L1_packed, L2_packed(0)), ..., nonce, ...)
   (l1_data_gas_packed(0) is absent from the Poseidon absorb sequence)

6. H₁ ≠ H₂.
   validate_transaction_hash(T, block_number, chain_id, H₁, ...) returns false
   because the only hash in possible_hashes is H₂.
   The peer rejects T or stores it under the wrong hash.
``` [8](#0-7) [9](#0-8)

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

**File:** crates/apollo_protobuf/src/converters/consensus_test.rs (L26-27)
```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
```

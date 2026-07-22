### Title
Protobuf Round-Trip of `ValidResourceBounds` Loses Variant, Producing a Different Transaction Hash — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

A V3 transaction submitted with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is accepted by the gateway and assigned hash H1 (computed over three resource-bound elements). When that transaction is later serialized to protobuf and deserialized on a peer node (consensus/block-sync path), the `ValidResourceBounds` variant silently changes from `AllResources` to `L1Gas`, causing the peer to recompute a different hash H2 over only two resource-bound elements. H1 ≠ H2, breaking the canonicalization invariant that every node must agree on the transaction hash.

### Finding Description

**Serialization side** — `From<ValidResourceBounds> for protobuf::ResourceBounds` [1](#0-0) 

When the variant is `AllResources` with `l2_gas = 0` and `l1_data_gas = 0`, the serializer faithfully writes all three fields as zero into the protobuf message.

**Deserialization side** — `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` [2](#0-1) 

The deserializer applies a heuristic: if both `l2_gas` and `l1_data_gas` are zero it returns `ValidResourceBounds::L1Gas(l1_gas)`, otherwise `AllResources`. A transaction that was originally `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` therefore deserializes as `L1Gas(X)` — the variant is lost.

**Hash computation** — `get_tip_resource_bounds_hash` [3](#0-2) 

The hash is computed over a different number of elements depending on the variant:

| Variant | Elements hashed |
|---|---|
| `AllResources` | `[tip, L1_GAS, L2_GAS, L1_DATA_GAS]` — 4 felts |
| `L1Gas` | `[tip, L1_GAS, L2_GAS]` — 3 felts |

Even when `l2_gas = 0` and `l1_data_gas = 0`, the `AllResources` path includes the zero-valued `L1_DATA_GAS` element in the Poseidon hash, while the `L1Gas` path omits it entirely. The two hashes are therefore distinct.

**Concrete divergence:**
```
H1 = poseidon([tip, pack(L1_GAS,X), pack(L2_GAS,0), pack(L1_DATA_GAS,0)])
H2 = poseidon([tip, pack(L1_GAS,X), pack(L2_GAS,0)])
H1 ≠ H2
```

**Trigger path:**

1. User submits a V3 invoke/declare/deploy-account transaction via RPC with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`. The stateless validator accepts it because `l1_gas` is non-zero. [4](#0-3) 

2. The gateway computes hash H1 (AllResources, 3 resource bounds) and stores the transaction in the mempool.

3. The transaction is included in a block and propagated via the consensus/block-sync P2P path, which serializes it through `From<ValidResourceBounds> for protobuf::ResourceBounds`.

4. The receiving peer deserializes via `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` and obtains `L1Gas(X)`.

5. The peer recomputes hash H2 (L1Gas, 2 resource bounds). H2 ≠ H1.

The P2P flow is confirmed by the transaction submission diagram: [5](#0-4) 

### Impact Explanation

Every node that deserializes the transaction from protobuf will compute a hash that differs from the hash computed by the originating node and stored in the block. This breaks the invariant that all nodes agree on the transaction hash, which is the canonical identifier used for receipts, events, state diffs, and block-hash computation. A peer that recomputes H2 while the block records H1 will either reject the block as invalid or silently store the transaction under the wrong hash, producing wrong receipts and wrong state.

This matches: **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload**, and potentially **Critical — Wrong state, receipt, event, class hash, or storage value from blockifier/syscall/execution logic for accepted input**.

### Likelihood Explanation

Any user can craft a valid V3 transaction with `l2_gas = 0` and `l1_data_gas = 0` (only `l1_gas` non-zero). The stateless validator explicitly accepts this case. The trigger requires no privilege and no special knowledge. The condition is met by any pre-0.13.3-style V3 transaction that happens to set only `l1_gas`.

### Recommendation

The deserialization heuristic in `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` must not use field values to infer the variant. The protobuf schema should carry an explicit discriminant (e.g., a `oneof` or a boolean `is_all_resources` flag) so the variant is preserved losslessly across the wire. Until the schema is updated, the deserializer should default to `AllResources` whenever `l1_data_gas` is present in the message (even if zero), reserving `L1Gas` only for messages that genuinely omit `l1_data_gas`.

The TODO comment at line 426 acknowledges this is a temporary compatibility shim: [6](#0-5) 

That shim must be resolved before the `AllResources`/`L1Gas` boundary is exposed to production traffic.

### Proof of Concept

```rust
use starknet_api::transaction::fields::{
    AllResourceBounds, ResourceBounds, ValidResourceBounds,
};
use starknet_api::block::GasPrice;
use starknet_api::execution_resources::GasAmount;
use starknet_api::transaction::Tip;
use starknet_api::transaction_hash::get_tip_resource_bounds_hash;
use apollo_protobuf::protobuf;

// Step 1: construct AllResources with l2_gas=0, l1_data_gas=0
let original = ValidResourceBounds::AllResources(AllResourceBounds {
    l1_gas: ResourceBounds { max_amount: GasAmount(100), max_price_per_unit: GasPrice(1) },
    l2_gas: ResourceBounds::default(),      // zero
    l1_data_gas: ResourceBounds::default(), // zero
});

// Step 2: compute hash on originating node (AllResources path → 3 bounds)
let h1 = get_tip_resource_bounds_hash(&original, &Tip(0)).unwrap();

// Step 3: serialize to protobuf and deserialize (simulates P2P round-trip)
let proto: protobuf::ResourceBounds = original.into();
let deserialized = ValidResourceBounds::try_from(proto).unwrap();

// deserialized is now L1Gas, NOT AllResources
assert!(matches!(deserialized, ValidResourceBounds::L1Gas(_)));

// Step 4: compute hash on receiving node (L1Gas path → 2 bounds)
let h2 = get_tip_resource_bounds_hash(&deserialized, &Tip(0)).unwrap();

// Step 5: hashes diverge
assert_ne!(h1, h2, "Transaction hash changes across protobuf round-trip");
```

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L479-487)
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

**File:** docs/diagrams/02-tx-submission-flow.md (L48-53)
```markdown
    MP->>Prop: add_transaction(InternalRpcTransaction)
    Prop->>Runner: broadcast (P2P)
    Runner->>GW_B: add_tx(GatewayInput)
    Note over GW_B: Same validation flow
    GW_B->>MP_B: add_tx(AddTransactionArgsWrapper)
```
```

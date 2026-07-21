### Title
`ValidResourceBounds::AllResources` with zero `l2_gas`/`l1_data_gas` silently collapses to `ValidResourceBounds::L1Gas` in protobuf deserialization, producing a divergent transaction hash - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf-to-`ValidResourceBounds` conversion in the P2P block-sync path silently maps `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` to `L1Gas(l1_gas=X)`. Because `get_tip_resource_bounds_hash` hashes a different number of resource felts for each variant (2 for `L1Gas`, 3 for `AllResources`), the two variants produce **different transaction hashes** for identical numeric values. A transaction whose hash was computed by the sequencer under `AllResources` will hash to a different value on any peer that deserializes it via protobuf, breaking hash canonicalization across the network.

### Finding Description

**Step 1 – The lossy protobuf conversion.**

In `crates/apollo_protobuf/src/converters/transaction.rs` lines 417–437, when deserializing a `protobuf::ResourceBounds` into `ValidResourceBounds`, the code applies a zero-check heuristic:

```rust
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant changes
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

This fires even when `l1_data_gas` is **explicitly present** in the protobuf message but carries the value zero. Any `AllResources` transaction whose `l2_gas` and `l1_data_gas` are both zero is silently re-classified as `L1Gas`.

**Step 2 – The hash divergence.**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` lines 187–211 hashes a **different number of elements** depending on the variant:

```rust
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 felts total
    ValidResourceBounds::AllResources(all) => {
        vec![get_concat_resource(&all.l1_data_gas, L1_DATA_GAS)?]     // 3 felts total
    }
});
```

For `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)`:
- Hash input = `poseidon([tip, packed(L1_GAS,X), packed(L2_GAS,0), packed(L1_DATA_GAS,0)])` → **H1**

For `L1Gas(l1_gas=X)` (same numeric values, different variant):
- Hash input = `poseidon([tip, packed(L1_GAS,X), packed(L2_GAS,0)])` → **H2 ≠ H1**

**Step 3 – The reachable trigger path.**

The sequencer always creates `InternalRpcInvokeTransactionV3` with `resource_bounds: AllResourceBounds`. When converted to `InvokeTransactionV3` for storage and P2P block sync, the conversion at `crates/starknet_api/src/rpc_transaction.rs` line 682 wraps it as `ValidResourceBounds::AllResources(tx.resource_bounds)`. If the user submitted `AllResourceBounds { l1_gas=X, l2_gas=0, l1_data_gas=0 }`, the sequencer computes hash **H1** (3-felt variant). When this block is propagated to peers via protobuf, the receiving peer's deserialization at line 431 converts it to `L1Gas(X)` and any subsequent hash computation yields **H2**.

**Step 4 – Existing gates do not prevent this.**

The `Serialize` impl for `ValidResourceBounds::AllResources` always emits the `L1DataGas` key in the JSON map, so the local DB round-trip is lossless. The divergence is **protobuf-specific**. The `StatelessTransactionValidator` enforces only an upper bound on `l2_gas.max_amount`, not a lower bound, so `l2_gas=0` passes stateless validation. With `validate_resource_bounds = false` (bootstrap mode) or for transactions that genuinely require zero L2 gas, such a transaction can be accepted and included in a block.

### Impact Explanation

Any peer that receives a block containing such a transaction via P2P block sync will deserialize it as `L1Gas` and compute hash **H2**. If the peer validates the transaction hash (e.g., during re-execution for proving, or in `validate_transaction_hash`), it will observe a mismatch against the committed hash **H1**. This causes:

- **Wrong state/receipt from blockifier re-execution**: the re-executed transaction uses `L1Gas` semantics (only L1 gas bounds checked, no L2 gas bounds, different `max_possible_fee`), producing a different execution outcome than the original.
- **P2P block sync rejects valid blocks**: peers that recompute the hash after deserialization will reject a legitimately sequenced block.
- **Consensus/proving split**: the sequencer and its peers operate on different hash values for the same transaction, breaking the canonicalization invariant required for proof generation.

This matches the allowed impact: *Wrong state, receipt, or revert result from blockifier/execution logic for accepted input* (Critical) and *Mempool/gateway/RPC admission rejects valid transactions* (High).

### Likelihood Explanation

The trigger requires a V3 invoke transaction with `AllResourceBounds { l2_gas.max_amount = 0, l1_data_gas.max_amount = 0 }` to be accepted and included in a block. This is possible when:
1. `validate_resource_bounds = false` (bootstrap/migration mode, explicitly supported in config).
2. A transaction that genuinely uses only L1 gas (e.g., a simple L1-only fee payment) sets both L2 and data gas bounds to zero.
3. The `create_for_testing` helper (`AllResourceBounds { l2_gas: {max_amount:0, ...}, l1_data_gas: {max_amount:0, ...} }`) is used in integration/OS-flow tests that exercise the P2P path.

The condition is narrow but reachable without any privileged access.

### Recommendation

Replace the zero-value heuristic in the protobuf conversion with an explicit variant discriminator. Options:

1. **Add a variant tag to the protobuf schema** (preferred): encode whether the transaction is `L1Gas` or `AllResources` explicitly in the protobuf message, so deserialization is unambiguous.

2. **Remove the heuristic collapse**: always deserialize to `AllResources` when all three resource fields are present in the protobuf message, regardless of their values. Only fall back to `L1Gas` when `l1_data_gas` is genuinely absent (old 0.13.2 wire format):

```rust
// Only collapse to L1Gas when l1_data_gas is absent (legacy 0.13.2 wire format).
Ok(if value.l1_data_gas.is_none() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

3. **Add an invariant test**: assert that `ValidResourceBounds::AllResources(X, 0, 0)` and `ValidResourceBounds::L1Gas(X)` produce the same hash, or document that they intentionally do not and gate the conversion accordingly.

### Proof of Concept

```
1. Construct an InvokeTransactionV3 with:
     resource_bounds = AllResources { l1_gas = {max_amount=1000, max_price=1},
                                      l2_gas = {max_amount=0, max_price=0},
                                      l1_data_gas = {max_amount=0, max_price=0} }

2. Compute hash H1 via get_invoke_transaction_v3_hash (AllResources path):
     tip_resource_bounds_hash = poseidon([tip, packed(L1_GAS,1000,1),
                                               packed(L2_GAS,0,0),
                                               packed(L1_DATA_GAS,0,0)])  // 3 felts

3. Serialize to protobuf::InvokeV3 (l1_data_gas field present, value = 0).

4. Deserialize via TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
     l1_data_gas.is_zero() && l2_gas.is_zero() == true
     → ValidResourceBounds::L1Gas({max_amount=1000, max_price=1})

5. Compute hash H2 via get_invoke_transaction_v3_hash (L1Gas path):
     tip_resource_bounds_hash = poseidon([tip, packed(L1_GAS,1000,1),
                                               packed(L2_GAS,0,0)])       // 2 felts

6. Assert H1 != H2.  ← canonicalization invariant broken
```

The divergence is confirmed by the branch logic in `get_tip_resource_bounds_hash` at `crates/starknet_api/src/transaction_hash.rs` lines 203–208: `L1Gas` extends with an empty vec while `AllResources` appends the `L1_DATA_GAS` packed felt, making the two Poseidon inputs structurally different even when all numeric values are identical. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/starknet_api/src/transaction_hash.rs (L187-211)
```rust
// An implementation of the SNIP: https://github.com/EvyatarO/SNIPs/blob/snip-8/SNIPS/snip-8.md
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L679-694)
```rust
impl From<InternalRpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: InternalRpcInvokeTransactionV3) -> Self {
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
```

**File:** crates/starknet_api/src/transaction/fields.rs (L363-367)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}
```

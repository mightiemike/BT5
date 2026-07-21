### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Different Transaction Hash Preimage — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter in `crates/apollo_protobuf/src/converters/transaction.rs` silently converts an `AllResources` variant (with zero `l2_gas` and `l1_data_gas`) into a `L1Gas` variant. Because `get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` produces a structurally different Poseidon preimage for these two variants (3 vs. 4 field elements), the transaction hash computed by the sequencer at ingestion time diverges from the hash any peer computes after receiving the same transaction over P2P state sync. This is a direct analog of the external report's invariant: the "accumulated" field (`l1_data_gas`) is silently dropped from the hash preimage at the conversion boundary, just as `queuedPerpSize` was dropped from the order submission.

---

### Finding Description

**Step 1 — Ingestion path always uses `AllResources`.**

`InternalRpcInvokeTransactionV3` stores resource bounds as `AllResourceBounds` and its `InvokeTransactionV3Trait` implementation always wraps them in `ValidResourceBounds::AllResources(...)`: [1](#0-0) 

The hash is therefore computed by `get_invoke_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash` with the `AllResources` variant. For `AllResources`, the inner Poseidon hash covers **four** elements: `[tip, L1_GAS_packed, L2_GAS_packed, L1_DATA_GAS_packed]`: [2](#0-1) 

**Step 2 — For `L1Gas`, the inner hash covers only three elements.**

When `resource_bounds` is `ValidResourceBounds::L1Gas(_)`, the `L1_DATA_GAS` term is omitted entirely: [3](#0-2) 

So `poseidon(tip, L1_GAS_packed, L2_GAS_packed)` ≠ `poseidon(tip, L1_GAS_packed, L2_GAS_packed, L1_DATA_GAS_packed(0))`. These are different field elements.

**Step 3 — The protobuf round-trip silently changes the variant.**

When the sequencer serializes an `AllResources` transaction to protobuf for P2P state sync, `l2_gas = 0` and `l1_data_gas = 0` are written as zero `ResourceLimits` messages. On the receiving side, `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` applies this logic: [4](#0-3) 

Because `l1_data_gas` is `optional` in the proto schema and defaults to zero when absent, and because the condition `l1_data_gas.is_zero() && l2_gas.is_zero()` is true for any `AllResources` transaction whose user set both to zero, the deserialized value is `ValidResourceBounds::L1Gas(l1_gas)` — not `AllResources`. The proto schema confirms `l1_data_gas` is optional: [5](#0-4) 

**Step 4 — The gateway accepts `AllResources` with zero `l2_gas` and `l1_data_gas`.**

The stateless validator explicitly allows a transaction where only `l1_gas` is non-zero and both `l2_gas` and `l1_data_gas` are zero: [6](#0-5) 

This is an unprivileged, normal transaction submission path.

**Step 5 — Divergent hashes.**

| Path | Variant | Inner Poseidon inputs | Hash |
|---|---|---|---|
| Sequencer ingestion | `AllResources { l1_gas=X, l2_gas=0, l1_data_gas=0 }` | `[tip, L1_GAS(X), L2_GAS(0), L1_DATA_GAS(0)]` | H1 |
| Peer after P2P deserialization | `L1Gas(X)` | `[tip, L1_GAS(X), L2_GAS(0)]` | H2 |

H1 ≠ H2 because Poseidon is sensitive to the number of inputs.

---

### Impact Explanation

**Scenario A — Peer re-validates hash.** The peer receives `(transaction_data, H1)` from the protobuf message. It deserializes `resource_bounds` as `L1Gas`, recomputes H2, finds H2 ≠ H1, and rejects the block. A single user-submitted transaction with zero `l2_gas`/`l1_data_gas` causes every syncing peer to reject the block containing it — a chain-halting DoS against state sync.

**Scenario B — Peer trusts the protobuf hash.** The peer stores the transaction with hash H1 but `resource_bounds = L1Gas(X)`. When the blockifier or prover re-executes the transaction, it uses `GasVectorComputationMode::NoL2Gas` (the mode for `L1Gas`) instead of `GasVectorComputationMode::All` (the mode for `AllResources`). This changes fee validation, gas accounting, and execution behavior — producing wrong receipts, wrong fees, and wrong state diffs relative to what the sequencer committed.

Both scenarios match the allowed impact scope:
- **High**: "Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."
- **Critical** (Scenario B): "Wrong state, receipt, event … or revert result from blockifier/syscall/execution logic for accepted input."

---

### Likelihood Explanation

The trigger requires no privilege. Any user submitting a standard `InvokeV3` transaction with `l1_gas > 0`, `l2_gas = 0`, `l1_data_gas = 0` — a configuration the gateway explicitly accepts — produces the divergent hash. The condition is reachable on every network (mainnet, testnet) without any special access.

---

### Recommendation

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter must not silently downgrade `AllResources` to `L1Gas`. The variant must be determined by the **protocol version** of the transaction (e.g., a version field or a flag in the enclosing message), not by whether the numeric values happen to be zero. Concretely:

1. Remove the `if l1_data_gas.is_zero() && l2_gas.is_zero()` branch from the P2P deserialization path for transactions that were submitted under protocol ≥ 0.13.3. Always produce `AllResources` for such transactions.
2. Reserve `L1Gas` deserialization only for transactions explicitly tagged as pre-0.13.3 (e.g., by transaction version or a dedicated proto field).
3. Add a round-trip hash-equality test: serialize an `AllResources { l1_gas=X, l2_gas=0, l1_data_gas=0 }` transaction, deserialize it, recompute the hash, and assert it equals the original.

---

### Proof of Concept

```
1. Construct RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
     }

2. Submit to gateway → accepted (stateless validator passes for non-zero l1_gas).

3. Sequencer computes hash H1 via get_invoke_transaction_v3_hash with
   ValidResourceBounds::AllResources → get_tip_resource_bounds_hash produces
   poseidon(tip, L1_GAS_packed(1000,1), L2_GAS_packed(0,0), L1_DATA_GAS_packed(0,0)).

4. Transaction is included in block B. Sequencer serializes it to protobuf::InvokeV3
   with resource_bounds = { l1_gas: Some(1000,1), l2_gas: Some(0,0), l1_data_gas: Some(0,0) }.

5. Peer receives block B via P2P state sync. Deserializes resource_bounds:
     l1_data_gas = Some(0,0).unwrap_or_default() → zero
     l2_gas = zero
     l1_data_gas.is_zero() && l2_gas.is_zero() → true
     → ValidResourceBounds::L1Gas(ResourceBounds { max_amount: 1000, max_price_per_unit: 1 })

6. Peer computes hash H2 via get_invoke_transaction_v3_hash with ValidResourceBounds::L1Gas
   → get_tip_resource_bounds_hash produces
   poseidon(tip, L1_GAS_packed(1000,1), L2_GAS_packed(0,0))   [only 3 elements].

7. H1 ≠ H2.
   - If peer re-validates: block B rejected → state sync DoS.
   - If peer trusts H1: transaction stored with L1Gas bounds → wrong gas mode,
     wrong fee accounting, wrong execution result on re-execution.
```

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/starknet_api/src/transaction_hash.rs (L202-210)
```rust
    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
```

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

**File:** crates/apollo_protobuf/src/proto/p2p/proto/transaction.proto (L13-19)
```text
message ResourceBounds {
    ResourceLimits l1_gas = 1;
    // This can be None only in transactions that don't support l2 gas.
    // Starting from 0.14.0, MempoolTransaction and ConsensusTransaction shouldn't have None here.
    optional ResourceLimits l1_data_gas = 2;
    ResourceLimits l2_gas = 3;
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

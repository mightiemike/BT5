Now I have enough context to write the finding. The key issue is in the `ValidResourceBounds` protobuf deserializer and how it interacts with the transaction hash computation.

### Title
`ValidResourceBounds` Protobuf Deserializer Silently Downgrades `AllResources` to `L1Gas` When Gas Fields Are Zero, Producing a Wrong Transaction Hash - (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter in the P2P block-sync path silently collapses `AllResources { l2_gas: 0, l1_data_gas: 0 }` into `L1Gas`. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` element in the Poseidon preimage only for `AllResources`, the same on-chain transaction produces two different hash values depending on which code path deserializes it. A transaction admitted and hashed by the gateway as `AllResources` will be re-hashed as `L1Gas` by any node that receives it over P2P block sync, yielding a divergent `TransactionHash`.

### Finding Description

**Step 1 – Submission and canonical hash.**

A user submits a V3 invoke transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`. The gateway converts it to `InternalRpcInvokeTransactionV3`, whose `resource_bounds()` method always returns `ValidResourceBounds::AllResources(self.resource_bounds)`. [1](#0-0) 

The hash is then computed by `get_invoke_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash`: [2](#0-1) 

Inside `get_tip_resource_bounds_hash`, the `AllResources` branch appends a third element — the `L1_DATA_GAS` concat — to the Poseidon input: [3](#0-2) 

So the canonical hash **H₁** is `Poseidon(tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat)`.

**Step 2 – P2P block-sync deserialization.**

When the block containing this transaction is synced to another node, the transaction is deserialized from protobuf using `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`: [4](#0-3) 

Because `l2_gas = 0` and `l1_data_gas = 0` (or `None`, defaulted to zero via `unwrap_or_default`), the condition on line 431 is satisfied and the converter returns `ValidResourceBounds::L1Gas(l1_gas)` — discarding the `AllResources` variant entirely.

**Step 3 – Wrong hash H₂.**

When the receiving node recomputes the transaction hash from the deserialized object, `get_tip_resource_bounds_hash` now takes the `L1Gas` branch: [5](#0-4) 

The `L1Gas` arm contributes an empty `vec![]`, so the Poseidon input is only `(tip, L1_GAS_concat, L2_GAS_concat)` — **two resource elements instead of three**. The resulting hash **H₂ ≠ H₁**.

**The broken invariant** (analog to the external bug): the `ValidResourceBounds` variant is the "exchange rate" that determines how many elements enter the hash preimage. It is fixed at submission time (`AllResources`) but silently "appreciates" (changes) to `L1Gas` at deserialization time, making the recorded hash impossible to reproduce from the deserialized representation.

### Impact Explanation

Any node that receives the block via P2P sync and recomputes transaction hashes — for block-hash verification, transaction commitment, or RPC `starknet_getTransactionByHash` responses — will produce a hash that does not match the canonical on-chain hash. This constitutes **wrong hash binding** in the transaction conversion path, and can cause:

- Block hash / transaction commitment verification failures on syncing nodes.
- RPC responses returning an authoritative-looking but incorrect `transaction_hash`.
- Divergent state between the sequencer (which used `AllResources`) and syncing nodes (which use `L1Gas`).

### Likelihood Explanation

Any V3 transaction with `l2_gas = 0` and `l1_data_gas = 0` triggers this path. This is a common configuration: client-side-proving transactions explicitly set all `max_price_per_unit` fields to zero, and many transactions set `l2_gas` to zero. The condition is reachable by any unprivileged user submitting a standard V3 transaction.

### Recommendation

The `ValidResourceBounds` protobuf deserializer must preserve the original variant. The simplest fix is to require `l1_data_gas` to be present (removing the `unwrap_or_default`) and always return `AllResources` when all three fields are present — regardless of whether their values are zero. The TODO comment on line 426 already acknowledges this:

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
```

The backward-compatibility window for 0.13.2 (which genuinely lacks `l1_data_gas`) should be handled by a separate, version-gated code path rather than by silently collapsing the variant based on zero-value heuristics.

### Proof of Concept

```
1. Submit V3 invoke tx:
     resource_bounds = AllResourceBounds {
         l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     }

2. Gateway computes H₁ via get_tip_resource_bounds_hash(AllResources):
     preimage = [tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat]  // 3 resource elements
     H₁ = Poseidon(preimage)

3. Block is synced via P2P. Receiving node deserializes:
     TryFrom<protobuf::ResourceBounds> for ValidResourceBounds
     → l2_gas.is_zero() && l1_data_gas.is_zero()  → L1Gas(l1_gas)   // variant changed

4. Receiving node computes H₂ via get_tip_resource_bounds_hash(L1Gas):
     preimage = [tip, L1_GAS_concat, L2_GAS_concat]                  // only 2 resource elements
     H₂ = Poseidon(preimage)

5. H₁ ≠ H₂  →  hash mismatch; block/tx commitment verification fails
               or RPC returns wrong transaction_hash for this tx.
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

**File:** crates/starknet_api/src/transaction_hash.rs (L375-376)
```rust
    let tip_resource_bounds_hash =
        get_tip_resource_bounds_hash(&transaction.resource_bounds(), transaction.tip())?;
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

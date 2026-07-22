### Title
Wrong transaction hash computed for V3 transactions with zero L2/data-gas bounds after P2P block-sync deserialization — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf-to-`ValidResourceBounds` converter used in the P2P block-sync path silently downgrades an `AllResources` bound to `L1Gas` whenever `l2_gas` and `l1_data_gas` are both zero. Because `get_tip_resource_bounds_hash` produces a structurally different Poseidon hash for `L1Gas` (two resource elements) versus `AllResources` (three resource elements, including a zero-valued `L1_DATA_GAS` concatenation), any V3 transaction that was originally signed and admitted with `AllResources{l2_gas=0, l1_data_gas=0}` will have its hash recomputed to a different value after deserialization. This breaks hash validation for those transactions on every node that receives the block via P2P sync.

---

### Finding Description

**Step 1 — The gateway admits `AllResources` with zero L2/data-gas.**

The stateless validator accepts a V3 transaction whose `AllResourceBounds` has only `l1_gas` non-zero:

```rust
// stateless_transaction_validator_test.rs (test case valid_l1_gas)
resource_bounds: AllResourceBounds {
    l1_gas: NON_EMPTY_RESOURCE_BOUNDS,
    ..Default::default()   // l2_gas = 0, l1_data_gas = 0
},
```

The gateway computes the transaction hash using `get_tip_resource_bounds_hash` with `ValidResourceBounds::AllResources(...)`. Because the variant is `AllResources`, the function appends a third element — the zero-valued `L1_DATA_GAS` concatenation — to the Poseidon hash chain:

```rust
// crates/starknet_api/src/transaction_hash.rs  lines 203-208
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // ← 2 elements
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // ← 3 elements
    }
});
```

The resulting hash `H_AllResources = poseidon(tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat_zero)` is stored in the block and signed by the user.

**Step 2 — The P2P block-sync converter silently changes the variant.**

When a peer receives the block and deserializes the transaction's resource bounds from protobuf, the converter in `transaction.rs` applies a heuristic:

```rust
// crates/apollo_protobuf/src/converters/transaction.rs  lines 431-435
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant changed
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

For the transaction above (`l2_gas=0`, `l1_data_gas=0`), the deserialized variant is `L1Gas`, not `AllResources`.

**Step 3 — Hash recomputation produces a different value.**

Any subsequent call to `get_transaction_hash` or `validate_transaction_hash` on the deserialized transaction now computes:

```
H_L1Gas = poseidon(tip, L1_GAS_concat, L2_GAS_concat)   // only 2 elements
```

This is a different Poseidon digest from `H_AllResources`. The `validate_transaction_hash` function (called from `apollo_storage/src/body/mod.rs`) will therefore return `false` for a valid, correctly-signed transaction, or the wrong hash will be stored/served by the RPC layer.

---

### Impact Explanation

**Impact: High** — Transaction conversion or signature/hash logic binds the wrong hash/executable payload.

A V3 transaction with `AllResources{l2_gas=0, l1_data_gas=0}` is valid and accepted by the gateway. After P2P block-sync deserialization, the hash computed from the transaction body no longer matches the hash stored in the block. This causes:

1. **Block/transaction hash validation failures** on syncing nodes — `validate_transaction_hash` returns `false` for a legitimately included transaction, potentially causing the syncing node to reject or mishandle the block.
2. **Wrong hash served by the RPC layer** — `starknet_getTransactionByHash` and related endpoints recompute the hash from the stored `Transaction` object; the recomputed hash diverges from the canonical one, producing an authoritative-looking wrong value.

---

### Likelihood Explanation

**Likelihood: Low** — The trigger requires a user to submit a V3 transaction with `AllResourceBounds` where both `l2_gas` and `l1_data_gas` are zero (only `l1_gas` is non-zero). This is a valid and accepted configuration (pre-0.13.3 style), so it can be triggered by any unprivileged user who submits such a transaction. The condition is narrow but entirely within normal protocol usage.

---

### Recommendation

Remove the heuristic downgrade in the protobuf converter. The variant should be preserved as `AllResources` whenever the wire message was encoded as `AllResources` (i.e., when `l1_data_gas` is present in the protobuf message, even if zero):

```diff
// crates/apollo_protobuf/src/converters/transaction.rs
-    let l1_data_gas = value.l1_data_gas.unwrap_or_default();
-    let l1_gas: ResourceBounds = l1_gas.try_into()?;
-    let l2_gas: ResourceBounds = l2_gas.try_into()?;
-    let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
-    Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
-        ValidResourceBounds::L1Gas(l1_gas)
-    } else {
-        ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
-    })
+    let l1_gas: ResourceBounds = l1_gas.try_into()?;
+    let l2_gas: ResourceBounds = l2_gas.try_into()?;
+    Ok(match value.l1_data_gas {
+        // No l1_data_gas field present → legacy pre-0.13.3 transaction → L1Gas variant
+        None if l2_gas.is_zero() => ValidResourceBounds::L1Gas(l1_gas),
+        // l1_data_gas field present (even if zero) → AllResources variant, preserving hash domain
+        _ => {
+            let l1_data_gas: ResourceBounds =
+                value.l1_data_gas.unwrap_or_default().try_into()?;
+            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
+        }
+    })
```

The key invariant to enforce: the `ValidResourceBounds` variant must be determined by the **presence or absence of the `l1_data_gas` field in the protobuf message**, not by whether its decoded value happens to be zero. This matches the semantic distinction already encoded in `get_tip_resource_bounds_hash`.

---

### Proof of Concept

1. Submit a V3 invoke transaction with `AllResourceBounds { l1_gas: {max_amount: 1000, max_price: 1}, l2_gas: {0,0}, l1_data_gas: {0,0} }` to the gateway. The gateway accepts it and computes hash `H_A = poseidon(0, L1_GAS_concat, L2_GAS_concat_zero, L1_DATA_GAS_concat_zero)`.

2. The transaction is included in a block. The block is propagated via P2P.

3. A syncing peer deserializes the transaction. The converter at [1](#0-0)  produces `ValidResourceBounds::L1Gas(l1_gas)` because `l1_data_gas.is_zero() && l2_gas.is_zero()`.

4. `get_tip_resource_bounds_hash` is called on the deserialized transaction. The branch at [2](#0-1)  takes the `L1Gas` arm, producing `H_B = poseidon(0, L1_GAS_concat, L2_GAS_concat_zero)` — only two resource elements.

5. `H_A ≠ H_B` because the Poseidon hash over three elements differs from the hash over two elements. `validate_transaction_hash` [3](#0-2)  returns `false` for the valid transaction, or the RPC layer serves `H_B` as the canonical hash instead of `H_A`.

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L431-435)
```rust
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
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

**File:** crates/starknet_api/src/transaction_hash.rs (L203-208)
```rust
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });
```

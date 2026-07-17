### Title
`GlobalContractDistributionReceiptV1::nonce()` Always Returns 0, Allowing Stale V1 Distribution Receipts to Overwrite Newer Deployments Across Shards — (`runtime/runtime/src/global_contracts.rs`)

---

### Summary

`GlobalContractDistributionReceipt::V1` carries no nonce field and its `nonce()` accessor unconditionally returns `0`. The idempotency guard `check_and_update_nonce` uses the comparison `incoming_nonce < stored_nonce` and then writes `set_nonce(state_update, nonce_key, incoming_nonce)`. For any V1 receipt, `incoming_nonce = 0`, so after the first V1 receipt is processed the stored nonce remains `0`. Every subsequent V1 receipt for the same contract identifier passes the same check (`0 < 0` is false → accepted), allowing an older V1 distribution receipt to overwrite a newer one on any shard where the stored nonce has not yet been advanced by a V2 receipt.

---

### Finding Description

`GlobalContractDistributionReceipt` has two Borsh-discriminated variants: [1](#0-0) 

`V1` has no `nonce` field; its accessor returns a hard-coded sentinel: [2](#0-1) 

The idempotency guard in `apply_distribution_current_shard` calls `check_and_update_nonce`: [3](#0-2) 

`check_and_update_nonce` reads the stored nonce, compares, and writes back `incoming_nonce`: [4](#0-3) 

For a V1 receipt: `incoming_nonce = 0`. After the first V1 receipt is processed, `set_nonce` writes `0` to `TrieKey::GlobalContractNonce`. The stored nonce is still `0`. A second V1 receipt for the same identifier arrives: `0 < 0` is `false` → the guard passes → the second receipt's code is written over the first. Because `forward_distribution_next_shard` is called **unconditionally** regardless of whether the current-shard nonce check passed or failed: [5](#0-4) 

every V1 receipt propagates to all shards. On each shard where `stored_nonce = 0`, the V1 receipt is applied. The `forward` method preserves the `V1` variant, so forwarded copies also carry `nonce = 0`: [6](#0-5) 

**Concrete scenario (AccountId deploy mode):**

1. Before the nonce feature activates, a user calls `DeployGlobalContract` twice for the same account ID, producing V1 receipts R1 (old code) and R2 (new code), both entering the delayed/buffered queue.
2. The protocol upgrades; `initiate_distribution` now creates V2 receipts, but R1 and R2 are already serialised as V1 in the trie.
3. On shard S, R2 is processed first: `incoming=0, stored=0` → accepted, `stored_nonce` written as `0`, new code installed.
4. R1 arrives on shard S: `incoming=0, stored=0` → `0 < 0` is false → **accepted**, old code overwrites new code.

The `increment_nonce` path (used at deploy time) correctly advances the state nonce to prevent duplicate initiations: [7](#0-6) 

But this only guards against duplicate *initiations*; it does not protect against V1 receipts already in flight whose `nonce()` is permanently `0`.

---

### Impact Explanation

`GlobalContractDistributionReceipt` is the mechanism by which a single contract deployment is replicated to every shard. The "global" invariant requires that all shards hold the same code for a given identifier. When two V1 receipts for the same `AccountId` identifier race across shards, the shard-by-shard ordering of delayed-queue draining determines which code wins on each shard independently. The result is **shard-layout inconsistency**: different shards execute different bytecode for the same global contract identifier. Every account that has called `UseGlobalContract` with that identifier will execute whichever code happened to arrive last on its shard, breaking determinism and the global-contract correctness guarantee. If the older code contains a vulnerability that the newer deployment was meant to fix, accounts on affected shards remain exposed.

---

### Likelihood Explanation

The window is the gap between the activation of the global-contract feature and the activation of the nonce feature. Any user who deployed the same `AccountId`-mode global contract more than once during that window, and whose receipts were delayed (e.g., by congestion), has V1 receipts in the trie queue. The CHANGELOG confirms the nonce feature was added as a subsequent fix:

> "Add nonce-based idempotency for global contract distribution receipts … preventing race conditions during multiple distribution attempts for the same contract."

V1 receipts forwarded via `forward()` remain V1 and propagate to all shards, so the race can manifest on every shard independently.

---

### Recommendation

In `check_and_update_nonce`, treat a V1 receipt (detected via `global_contract_data.maybe_nonce().is_none()`) as permanently stale if the stored nonce is already `> 0`. If the stored nonce is `0`, advance it to `1` after accepting a V1 receipt so that no subsequent V1 receipt can overwrite it:

```rust
let incoming_nonce = global_contract_data.nonce(); // 0 for V1
if incoming_nonce < stored_nonce {
    return Ok(false);
}
// Advance to at least 1 so a second V1 receipt (also nonce=0) is rejected.
let effective_stored = incoming_nonce.max(1);
set_nonce(state_update, nonce_key, effective_stored);
Ok(true)
```

Alternatively, gate `forward_distribution_next_shard` on `is_nonce_fresh` so that stale receipts are not propagated further, reducing the blast radius.

---

### Proof of Concept

1. `GlobalContractDistributionReceiptV1::nonce()` returns `0`: [8](#0-7) 

2. `check_and_update_nonce` writes `incoming_nonce` (= `0`) back to state, leaving `stored_nonce = 0`: [9](#0-8) 

3. A second V1 receipt for the same identifier: `incoming_nonce=0`, `stored_nonce=0` → `0 < 0` is `false` → guard passes → `apply_distribution_current_shard` writes the old code over the new code: [10](#0-9) 

4. `forward_distribution_next_shard` is called unconditionally, propagating the stale V1 receipt to every remaining shard where `stored_nonce = 0`: [11](#0-10)

### Citations

**File:** core/primitives/src/receipt.rs (L875-878)
```rust
pub enum GlobalContractDistributionReceipt {
    V1(GlobalContractDistributionReceiptV1) = 0,
    V2(GlobalContractDistributionReceiptV2) = 1,
}
```

**File:** core/primitives/src/receipt.rs (L949-956)
```rust
    /// Returns the nonce of the distribution.
    /// V1 receipts return 0, V2 receipts return their stored nonce.
    pub fn nonce(&self) -> u64 {
        match &self {
            Self::V1(_) => 0,
            Self::V2(v2) => v2.nonce,
        }
    }
```

**File:** core/primitives/src/receipt.rs (L968-981)
```rust
    pub fn forward(&self, target_shard: ShardId, already_delivered_shards: Vec<ShardId>) -> Self {
        match self {
            Self::V1(v1) => Self::V1(GlobalContractDistributionReceiptV1 {
                target_shard,
                already_delivered_shards,
                ..v1.clone()
            }),
            Self::V2(v2) => Self::V2(GlobalContractDistributionReceiptV2 {
                target_shard,
                already_delivered_shards,
                ..v2.clone()
            }),
        }
    }
```

**File:** runtime/runtime/src/global_contracts.rs (L126-138)
```rust
    let compute =
        apply_distribution_current_shard(receipt, global_contract_data, apply_state, state_update)?;
    forward_distribution_next_shard(
        receipt,
        global_contract_data,
        apply_state,
        epoch_info_provider,
        state_update,
        receipt_sink,
        receipt_to_tx,
    )?;

    Ok(compute)
```

**File:** runtime/runtime/src/global_contracts.rs (L173-187)
```rust
fn increment_nonce(
    state_update: &mut TrieUpdate,
    id: &GlobalContractIdentifier,
) -> Result<u64, RuntimeError> {
    let identifier: GlobalContractCodeIdentifier = id.clone().into();

    let nonce_key = TrieKey::GlobalContractNonce { identifier };
    let stored_nonce = get_nonce(state_update, &nonce_key)?;

    let new_nonce = stored_nonce.checked_add(1).ok_or_else(|| {
        RuntimeError::UnexpectedIntegerOverflow("increment_global_contract_nonce".into())
    })?;
    set_nonce(state_update, nonce_key, new_nonce);
    Ok(new_nonce)
}
```

**File:** runtime/runtime/src/global_contracts.rs (L202-205)
```rust
    let is_nonce_fresh = check_and_update_nonce(global_contract_data, &identifier, state_update)?;
    if !is_nonce_fresh {
        return Ok(0);
    }
```

**File:** runtime/runtime/src/global_contracts.rs (L208-211)
```rust
    let trie_key = TrieKey::GlobalContractCode { identifier };
    let code_len = global_contract_data.code().len() as u64;
    state_update.set(trie_key, global_contract_data.code().to_vec());
    state_update.commit(StateChangeCause::ReceiptProcessing { receipt_hash: receipt.get_hash() });
```

**File:** runtime/runtime/src/global_contracts.rs (L238-256)
```rust
fn check_and_update_nonce(
    global_contract_data: &GlobalContractDistributionReceipt,
    identifier: &GlobalContractCodeIdentifier,
    state_update: &mut TrieUpdate,
) -> Result<bool, RuntimeError> {
    let nonce_key = TrieKey::GlobalContractNonce { identifier: identifier.clone() };
    let stored_nonce = get_nonce(state_update, &nonce_key)?;
    let incoming_nonce = global_contract_data.nonce();

    // Allow the same nonce since the nonce is updated immediately when
    // initiating distribution to prevent multiple distributions with the same
    // nonce from being initiated.
    if incoming_nonce < stored_nonce {
        return Ok(false);
    }

    set_nonce(state_update, nonce_key, incoming_nonce);
    Ok(true)
}
```

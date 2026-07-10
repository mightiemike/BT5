### Title
Unprotected `run_mainchain_gc()` Allows Any Caller to Force-Prune Canonical Chain Headers, Corrupting SPV Proof Results and Blocking Reorgs — (`File: contract/src/lib.rs`)

---

### Summary

`BtcLightClient::run_mainchain_gc()` is a state-mutating function that permanently deletes mainchain block headers from all three canonical-chain indexes and advances `mainchain_initial_blockhash`. It carries only a `#[pause]` decorator, which restricts calls only when the contract is paused. When the contract is running normally, **any unprivileged NEAR account can call it with an arbitrary `batch_size`**. The intended design is that GC is paced and triggered exclusively by the access-controlled `submit_blocks` path. An attacker bypasses that pacing, force-pruning all eligible headers in one transaction, which permanently breaks SPV proof verification for those blocks and can prevent legitimate chain reorganizations.

---

### Finding Description

`submit_blocks` is correctly guarded with `#[trusted_relayer]`, restricting it to authorized relayers. Inside `submit_blocks`, GC is triggered as:

```rust
self.run_mainchain_gc(num_of_headers);   // batch_size == number of submitted headers
``` [1](#0-0) 

This means GC is paced: a relayer submitting one block at a time removes at most one old header per call.

`run_mainchain_gc` itself, however, carries no equivalent guard:

```rust
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
``` [2](#0-1) 

`#[pause]` only blocks calls when the contract is paused. When the contract is live, the function is open to any caller. The attacker supplies `batch_size = u64::MAX`. Inside the function:

```rust
let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
``` [3](#0-2) 

With `batch_size = u64::MAX`, `selected_amount_to_remove` equals `total_amount_to_remove` — every block eligible for GC is deleted in one transaction. For each removed height, `remove_block_header` permanently erases the entry from `mainchain_header_to_height` and `headers_pool`, and `mainchain_height_to_header` is also cleared:

```rust
self.remove_block_header(blockhash);
self.mainchain_height_to_header.remove(&height);
``` [4](#0-3) 

```rust
fn remove_block_header(&mut self, header_block_hash: &H256) {
    self.mainchain_header_to_height.remove(header_block_hash);
    self.headers_pool.remove(header_block_hash);
}
``` [5](#0-4) 

Finally, `mainchain_initial_blockhash` is advanced to the new oldest block, making the deletion irreversible:

```rust
self.mainchain_initial_blockhash = self
    .mainchain_height_to_header
    .get(&end_removal_height)
    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
``` [6](#0-5) 

---

### Impact Explanation

**1. SPV proof invalidation.** Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` look up the target block in `mainchain_header_to_height`:

```rust
let target_block_height = self
    .mainchain_header_to_height
    .get(&args.tx_block_blockhash)
    .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
``` [7](#0-6) 

If the attacker has force-pruned that block, the call panics. A downstream NEAR contract that calls `verify_transaction_inclusion` to gate a payment or unlock receives a cross-contract panic instead of a boolean result — its logic is broken for any block the attacker chose to prune.

**2. Reorg prevention.** The project's own documentation states: *"If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound`."* [8](#0-7) 

An attacker who force-prunes blocks near an active fork point permanently prevents the legitimate chain from reorganizing to the heavier fork, corrupting the canonical-chain view held by the contract.

**3. Canonical state corruption.** `mainchain_initial_blockhash` is permanently advanced by the attacker, altering the contract's authoritative record of the oldest tracked block. This affects `get_mainchain_size`, `get_last_n_blocks_hashes`, and any consumer that relies on the retention window.

---

### Likelihood Explanation

**High.** The attacker needs no privileged role, no staked deposit, and no special knowledge beyond the contract's public ABI. A single NEAR transaction calling `run_mainchain_gc` with `batch_size = u64::MAX` is sufficient. The attack is executable at any time the contract is not paused, which is its normal operating state.

---

### Recommendation

Add a role-based access control guard to `run_mainchain_gc` so it can only be called by authorized relayers or a designated admin role — mirroring the `#[trusted_relayer]` guard already applied to `submit_blocks`. For example, restrict it to `Role::DAO` or a new dedicated `GCManager` role, and enforce the check at the function entry point rather than relying solely on the pause mechanism.

---

### Proof of Concept

1. Deploy the contract with `gc_threshold = 100` and seed it with 200 mainchain blocks via the authorized relayer. At this point `amount_of_headers_we_store = 200`, so `total_amount_to_remove = 100`.
2. From any unprivileged NEAR account (no role granted), call:
   ```
   run_mainchain_gc(batch_size: 18446744073709551615)
   ```
3. The function computes `selected_amount_to_remove = min(100, u64::MAX) = 100` and deletes blocks at heights `[initial_height, initial_height + 100)` from all three indexes in one transaction.
4. Now call `verify_transaction_inclusion` for any block in the deleted range. The call panics with `"block does not belong to the current main chain"` — the SPV proof is permanently broken for those blocks.
5. If a fork was building on any of the deleted heights, submit the fork tip. The `reorg_chain` walk panics with `"PrevBlockNotFound"` — the reorg is permanently blocked.

### Citations

**File:** contract/src/lib.rs (L175-181)
```rust
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L299-301)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
```

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```

**File:** contract/src/lib.rs (L393-393)
```rust
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
```

**File:** contract/src/lib.rs (L407-408)
```rust
                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
```

**File:** contract/src/lib.rs (L411-414)
```rust
            self.mainchain_initial_blockhash = self
                .mainchain_height_to_header
                .get(&end_removal_height)
                .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
```

**File:** contract/src/lib.rs (L659-661)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
```

**File:** contract/CLAUDE.md (L60-60)
```markdown
**Caveat**: If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound` when it cannot walk the chain back to the common ancestor. This means GC depth must be set conservatively relative to expected fork lengths
```

### Title
Unauthenticated `run_mainchain_gc` Allows Any Caller to Prematurely Prune Mainchain Headers, Breaking SPV Proof Verification - (File: `contract/src/lib.rs`)

### Summary
`BtcLightClient::run_mainchain_gc` is a public, state-mutating function with no caller authentication. Any unprivileged NEAR account can call it with an arbitrary `batch_size`, forcing premature deletion of mainchain block headers from `headers_pool` and `mainchain_height_to_header`. Downstream SPV proof verification (`verify_transaction_inclusion`, `verify_transaction_inclusion_v2`) and chain reorg resolution both depend on those headers being present and will panic or return incorrect results once the headers are gone.

### Finding Description
`run_mainchain_gc` is decorated only with `#[pause(except(roles(Role::UnrestrictedRunGC)))]`. [1](#0-0) 

The `#[pause]` attribute from `near_plugins` only gates execution when the contract is **paused** — it does not restrict who may call the function when the contract is running normally. No `#[trusted_relayer]`, `#[private]`, or role-check guard is applied. The caller-supplied `batch_size: u64` directly controls how many of the oldest mainchain headers are deleted in a single call. [2](#0-1) 

The function removes entries from `mainchain_height_to_header` and `headers_pool`, then advances `mainchain_initial_blockhash` forward. [3](#0-2) 

This is structurally identical to the xTRIBE analog: an unauthenticated public function that mutates shared state that other protocol layers depend on.

### Impact Explanation
`verify_transaction_inclusion` looks up the target block in `mainchain_header_to_height` and panics with `"block does not belong to the current main chain"` if the entry has been removed. [4](#0-3) 

`verify_transaction_inclusion_v2` delegates to the same path. [5](#0-4) 

Additionally, the project documentation explicitly warns that chain reorg resolution fails when GC has removed blocks near the fork point:

> "If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound` when it cannot walk the chain back to the common ancestor." [6](#0-5) 

An attacker can therefore:
1. Force-prune all headers older than `gc_threshold` at any time, invalidating in-flight SPV proofs for those blocks.
2. Trigger the pruning window earlier than the legitimate relayer would, causing `verify_transaction_inclusion` calls to panic for blocks that should still be available.
3. Destabilize reorg resolution by removing fork-point blocks before the relayer can complete a reorg.

The corrupted invariant is: **mainchain headers that are within the `gc_threshold` window and have not yet been verified by downstream consumers are permanently deleted from `headers_pool` and `mainchain_height_to_header`**.

### Likelihood Explanation
The entry path requires no special role, no deposit, and no privileged key — any NEAR account can call `run_mainchain_gc(batch_size)` directly. The attack is cheap (gas only) and repeatable. The window of opportunity exists whenever the mainchain size exceeds `gc_threshold`, which is the normal operating condition of a live deployment.

### Recommendation
Add a caller-authentication guard to `run_mainchain_gc` equivalent to the one on `submit_blocks`. The simplest fix is to add `#[trusted_relayer]` or an explicit role check (e.g., `Role::UnrestrictedRunGC` or `Role::DAO`) so that only authorized accounts can trigger GC externally. The internal call from `submit_blocks` (which is already authenticated) can bypass the guard via a private helper. [7](#0-6) 

### Proof of Concept
1. Deploy the contract and initialize it with enough blocks so that `mainchain_size > gc_threshold`.
2. From any unprivileged NEAR account (no role granted), call:
   ```
   run_mainchain_gc({ "batch_size": 999999999 })
   ```
3. Observe that the oldest `min(mainchain_size - gc_threshold, batch_size)` mainchain headers are deleted.
4. Attempt to call `verify_transaction_inclusion` for any of the pruned block hashes — the call panics with `"block does not belong to the current main chain"`.
5. Submit a fork that branches from a pruned block — the reorg walk panics with `"PrevBlockNotFound"`.

The test at `contract/tests/test_basics.rs:515-521` already demonstrates that a `user_account` holding only `UnrestrictedSubmitBlocks` (not `UnrestrictedRunGC`) can successfully call `run_mainchain_gc`, confirming the absence of any caller restriction. [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L166-169)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L299-302)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

```

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```

**File:** contract/src/lib.rs (L391-415)
```rust
        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);

            let start_removal_height = initial_blockheader.block_height;
            let end_removal_height = initial_blockheader.block_height + selected_amount_to_remove;
            env::log_str(&format!(
                "Num of blocks to remove {selected_amount_to_remove}"
            ));

            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
            }

            self.mainchain_initial_blockhash = self
                .mainchain_height_to_header
                .get(&end_removal_height)
                .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        }
```

**File:** contract/CLAUDE.md (L60-60)
```markdown
**Caveat**: If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound` when it cannot walk the chain back to the common ancestor. This means GC depth must be set conservatively relative to expected fork lengths
```

**File:** contract/tests/test_basics.rs (L515-521)
```rust
        let outcome = user_account
            .call(contract.id(), "run_mainchain_gc")
            .args_json(json!({"batch_size": 100}))
            .max_gas()
            .transact()
            .await?;
        assert!(outcome.is_success());
```

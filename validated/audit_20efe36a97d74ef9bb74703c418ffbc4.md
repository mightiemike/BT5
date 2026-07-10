### Title
Unauthorized Access to `run_mainchain_gc` Allows Any Caller to Prematurely Prune Block History — (`contract/src/lib.rs`)

### Summary
`run_mainchain_gc` is a public, state-mutating function on `BtcLightClient` that permanently deletes mainchain block headers from contract storage. It carries no caller access control: the only decorator is `#[pause(except(roles(Role::UnrestrictedRunGC)))]`, which is a **pause guard** (restricts callers only when the contract is paused), not an identity check. When the contract is running normally, any unprivileged NEAR account can call it with an attacker-controlled `batch_size`, immediately pruning all mainchain blocks above `gc_threshold` in a single transaction.

### Finding Description
The `near-plugins` `#[pause]` macro enforces that the function panics when the contract is paused, unless the caller holds the `UnrestrictedRunGC` role. It does **not** restrict who may call the function when the contract is unpaused. There is no `#[access_control]`, `#[private]`, or `#[trusted_relayer]` guard on `run_mainchain_gc`. [1](#0-0) 

The function removes mainchain block headers from `headers_pool` and `mainchain_height_to_header`, then advances `mainchain_initial_blockhash` to the new oldest block. The deletion is permanent and irreversible. [2](#0-1) 

The actual number of blocks removed is bounded by `min(total_amount_to_remove, batch_size)` where `total_amount_to_remove = amount_of_headers_we_store - gc_threshold`. Passing `batch_size = u64::MAX` causes the entire excess to be pruned in one call — far more aggressive than the normal path, which calls `run_mainchain_gc(num_of_headers)` with only the count of headers submitted in that batch. [3](#0-2) 

By contrast, `submit_blocks` — the only other entry point that triggers GC — is protected by `#[trusted_relayer]`, restricting it to authorized relayers. [4](#0-3) 

### Impact Explanation
1. **SPV proof breakage**: `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` look up the block header in `headers_pool` and `mainchain_header_to_height`. After an attacker-triggered GC prunes those entries, both functions panic with `"cannot find requested transaction block"` or `"block does not belong to the current main chain"` for any transaction in a pruned block. Any downstream contract or user relying on SPV proofs for recently confirmed transactions is permanently denied service for those blocks. [5](#0-4) 

2. **Chain reorganization breakage**: The project documentation explicitly states: *"If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound`."* An attacker can trigger aggressive GC to eliminate blocks near an active fork point, making any subsequent reorg permanently impossible. [6](#0-5) 

3. **Corrupted `mainchain_initial_blockhash`**: The pointer is advanced to the new oldest block, so the deletion cannot be undone without a full contract migration. [7](#0-6) 

### Likelihood Explanation
The entry point is a standard NEAR contract call requiring no deposit, no special role, and no prior state. Any NEAR account can execute it in a single transaction. The function is publicly documented as a "Public call to run GC on a mainchain," making it trivially discoverable. Likelihood is high.

### Recommendation
Add a caller access control check analogous to the `onlyMsc` fix applied in the referenced report. Concretely, restrict `run_mainchain_gc` to callers holding a designated role (e.g., `Role::DAO` or a new `GCManager` role) using the `near-plugins` `#[access_control_any]` attribute or an explicit `acl_is_granted` check at the top of the function body. The `#[pause(except(...))]` decorator should be retained alongside the new caller restriction.

### Proof of Concept
```
// Any unprivileged NEAR account executes this call:
user_account
    .call(contract.id(), "run_mainchain_gc")
    .args_json(json!({ "batch_size": u64::MAX }))
    .max_gas()
    .transact()
    .await?;

// All mainchain blocks above gc_threshold are now permanently deleted.
// Subsequent SPV proof call for any pruned block panics:
user_account
    .call(contract.id(), "verify_transaction_inclusion_v2")
    .args_borsh(ProofArgsV2 {
        tx_block_blockhash: pruned_block_hash,
        ..
    })
    .transact()
    .await?;
// → panics: "cannot find requested transaction block"
```

The test at `contract/tests/test_basics.rs` line 515–521 already demonstrates that an unprivileged `user_account` can call `run_mainchain_gc` successfully, confirming the absence of any caller restriction. [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L166-169)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L299-313)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));
```

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```

**File:** contract/src/lib.rs (L391-396)
```rust
        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);

            let start_removal_height = initial_blockheader.block_height;
            let end_removal_height = initial_blockheader.block_height + selected_amount_to_remove;
```

**File:** contract/src/lib.rs (L401-414)
```rust
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
```

**File:** contract/CLAUDE.md (L59-61)
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

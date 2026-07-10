### Title
Unrestricted `run_mainchain_gc` Allows Any Caller to Permanently Purge Verified Block Headers and Corrupt `mainchain_initial_blockhash` — (File: `contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` is a public, state-mutating NEAR contract method with no caller restriction. Any unprivileged NEAR account can invoke it with an arbitrarily large `batch_size`, causing the contract to permanently delete verified block headers from `headers_pool` and `mainchain_height_to_header`, and to advance `mainchain_initial_blockhash` to a newer block — all without authorization.

---

### Finding Description

`submit_blocks` is correctly gated behind `#[trusted_relayer]`, restricting block submission to authorized relayers. Internally, it calls `self.run_mainchain_gc(num_of_headers)` to incrementally prune old headers as new ones arrive. [1](#0-0) 

However, `run_mainchain_gc` is itself exposed as a standalone public method with no equivalent caller restriction: [2](#0-1) 

The only attribute present is `#[pause(except(roles(Role::UnrestrictedRunGC)))]`, which governs behavior when the contract is **paused** — it does not restrict who may call the function when the contract is **unpaused**. There is no `#[trusted_relayer]`, no `#[access_control]` role check, and no `require!(env::predecessor_account_id() == ...)` guard.

Inside the function, the caller-supplied `batch_size: u64` directly controls how many headers are removed: [3](#0-2) 

Specifically:
- `selected_amount_to_remove = min(total_amount_to_remove, batch_size)` — passing `u64::MAX` removes every header currently eligible for GC in a single call.
- Each eligible header is deleted from both `headers_pool` and `mainchain_height_to_header`.
- `mainchain_initial_blockhash` is then overwritten with the new oldest block.

The normal design intent is that GC advances incrementally — one batch per relayer submission — so that the window of stored headers shrinks gradually. An unprivileged caller bypasses this design entirely.

---

### Impact Explanation

An attacker calling `run_mainchain_gc(u64::MAX)` in a single transaction:

1. **Permanently deletes verified block headers** from `headers_pool` and `mainchain_height_to_header` for every block height in `[mainchain_initial_blockhash.height, tip.height - gc_threshold)`.
2. **Corrupts `mainchain_initial_blockhash`**: this sentinel value is advanced to the new oldest block without any authorized submission having occurred, breaking the invariant that it is only updated as a side-effect of a trusted relayer's `submit_blocks` call.
3. **Invalidates `verify_transaction_inclusion` and `verify_transaction_inclusion_v2`** for any transaction whose containing block was removed. Both verification methods look up the block in `headers_pool` and `mainchain_header_to_height`; after the unauthorized GC, those lookups panic or return `None`, causing verification to fail for legitimate consumers. [4](#0-3) 

The removal is bounded by `gc_threshold`, so headers within the threshold window are safe. However, the attacker can collapse the entire eligible window to zero in one call, which is qualitatively different from the intended incremental pruning and constitutes unauthorized, irreversible state mutation.

---

### Likelihood Explanation

The entry point is a plain public NEAR method requiring no stake, no role, and no deposit. Any NEAR account — including a freshly created one — can call it at any time the contract is unpaused. The call is cheap (gas only) and the damage is immediate and irreversible (NEAR storage deletions cannot be undone). Likelihood is **high**.

---

### Recommendation

Apply the same `#[trusted_relayer]` macro (or an equivalent role check such as `Role::DAO` or `Role::RelayerManager`) to `run_mainchain_gc` that is already applied to `submit_blocks`. If the function must remain callable by the public for storage-refund purposes, add an explicit allowlist check at the top of the function body, or remove the standalone public exposure and rely solely on the internal call from `submit_blocks`. [5](#0-4) 

---

### Proof of Concept

```rust
// Any unprivileged NEAR account can execute this:
fn test_unauthorized_gc() {
    // Assume contract has tip at height 60000, gc_threshold = 52704,
    // so headers at heights [0, 7296) are eligible for removal.
    //
    // A trusted relayer would normally remove these incrementally,
    // one batch per submit_blocks call.
    //
    // An attacker calls directly:
    btc_light_client.run_mainchain_gc(u64::MAX);
    // All 7296 eligible headers are now permanently deleted.
    // mainchain_initial_blockhash now points to height 7296.
    //
    // Any consumer calling verify_transaction_inclusion for a tx
    // in blocks 0..7296 will now panic with "block does not belong
    // to the current main chain".
}
``` [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** contract/src/lib.rs (L167-181)
```rust
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L310-313)
```rust
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

**File:** contract/src/lib.rs (L391-414)
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
```

### Title
Unrestricted `run_mainchain_gc` Allows Any Caller to Prune Mainchain Headers - (File: contract/src/lib.rs)

### Summary
`run_mainchain_gc` is a public, state-mutating function with no role-based access control. Any unprivileged NEAR account can call it with an attacker-controlled `batch_size`, immediately pruning all over-threshold mainchain headers and permanently invalidating transaction inclusion proofs for those blocks.

### Finding Description
The function `run_mainchain_gc` is decorated only with `#[pause(except(roles(Role::UnrestrictedRunGC)))]`. The `#[pause]` attribute restricts calls only when the contract is paused — it imposes no restriction on who may call the function when the contract is live. No role check (e.g., `Role::DAO`, `Role::RelayerManager`, or any equivalent) guards the function. [1](#0-0) 

The function is designed to be called internally by `submit_blocks` with `batch_size = num_of_headers` — a small, proportional value — so that GC happens gradually as the relayer submits new blocks. [2](#0-1) 

Because `run_mainchain_gc` is also a separately exported public method, any NEAR account can call it directly with `batch_size = u64::MAX`, bypassing the relayer-paced design entirely.

Inside the function, `selected_amount_to_remove` is bounded by `total_amount_to_remove = amount_of_headers_we_store - gc_threshold`. An attacker supplying `u64::MAX` causes the full over-threshold surplus to be removed in a single call. [3](#0-2) 

The removal loop deletes entries from `headers_pool`, `mainchain_height_to_header`, and `mainchain_header_to_height`, and advances `mainchain_initial_blockhash` to the new oldest block. [4](#0-3) 

### Impact Explanation
Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` look up the target block in `mainchain_header_to_height` and `headers_pool`. [5](#0-4) 

After an attacker forces full GC, any block that was pruned is permanently absent from these maps. A subsequent call to either verification function for a transaction in a pruned block will panic with `"block does not belong to the current main chain"` or `"cannot find requested transaction block"`. This breaks the bridge's core guarantee: that Bitcoin transaction inclusion can be verified on NEAR. Downstream contracts or users relying on these proofs receive a hard failure with no recourse, since the pruned headers cannot be re-submitted (the contract has no re-insertion path for already-GC'd mainchain blocks).

### Likelihood Explanation
The entry path requires no privilege, no staked tokens, and no special role. Any NEAR account can submit a single transaction calling `run_mainchain_gc` with `batch_size = u64::MAX`. The attack is cheap (one transaction, minimal gas beyond the loop iterations), deterministic, and irreversible. A griever targeting a specific pending proof need only observe the mempool or block explorer to time the call before the proof is submitted.

### Recommendation
Add a role-based access control guard to `run_mainchain_gc` so that only authorized accounts (e.g., holders of `Role::DAO` or `Role::RelayerManager`) can call it directly. The internal call from `submit_blocks` can be routed through a private helper that bypasses the role check, preserving the existing relayer-paced GC behavior.

### Proof of Concept
1. Deploy the contract and initialize it with `gc_threshold = 100` and submit 200 blocks via the authorized relayer. The mainchain now holds 200 headers; 100 are over the threshold.
2. From any unprivileged NEAR account, call:
   ```
   run_mainchain_gc(batch_size: 18446744073709551615)
   ```
3. The function computes `total_amount_to_remove = 200 - 100 = 100`, removes all 100 over-threshold headers, and advances `mainchain_initial_blockhash`.
4. Now call `verify_transaction_inclusion` for any transaction in one of the 100 pruned blocks. The call panics: `"block does not belong to the current main chain"`.
5. The pruned headers are gone permanently; no re-submission path exists.

### Citations

**File:** contract/src/lib.rs (L175-181)
```rust
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
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

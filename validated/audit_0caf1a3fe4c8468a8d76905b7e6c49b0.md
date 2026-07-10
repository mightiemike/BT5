### Title
Orphaned Fork Headers Permanently Lock Storage Deposits With No Recovery Path — (`contract/src/lib.rs`)

---

### Summary

`store_fork_header` writes fork-chain headers exclusively into `headers_pool`, bypassing both mainchain index maps. The GC routine `run_mainchain_gc` only iterates over `mainchain_height_to_header` and therefore never reaches these entries. Fork headers that do not accumulate enough chainwork to trigger a reorg remain in `headers_pool` indefinitely. The NEAR storage deposit paid by the submitter for those bytes is permanently locked in the contract with no function to reclaim it.

---

### Finding Description

When `submit_block_header_inner` determines that an incoming header extends a non-tip block, it calls `store_fork_header`: [1](#0-0) 

`store_fork_header` inserts only into `headers_pool`: [2](#0-1) 

It does **not** insert into `mainchain_height_to_header` or `mainchain_header_to_height`. The GC loop in `run_mainchain_gc` drives entirely off `mainchain_height_to_header`: [3](#0-2) 

Because fork headers are absent from `mainchain_height_to_header`, the GC loop never selects them. `reorg_chain` only promotes the *winning* fork to mainchain and removes displaced mainchain blocks at the same heights; it does not clean up any other fork entries that lost the competition: [4](#0-3) 

The result is that every fork header submitted but not ultimately promoted to mainchain remains in `headers_pool` forever. There is no public or privileged function that removes entries from `headers_pool` by fork-chain membership.

The storage deposit mechanism in `submit_blocks` charges the caller for the net storage increase: [5](#0-4) 

NEAR storage staking means the contract's account balance is reduced by `storage_byte_cost × bytes_used`. When orphaned fork headers are never freed, the deposit paid for them is never returned — it is permanently locked in the contract's storage staking balance with no recovery path.

---

### Impact Explanation

Every NEAR token paid as a storage deposit for a losing fork header is permanently locked. Over the operational lifetime of the contract — which is designed to track a live Bitcoin chain where natural short forks occur regularly — orphaned entries accumulate without bound. The submitter (relayer) has no mechanism to reclaim those tokens. Additionally, unbounded `headers_pool` growth increases the cost of every subsequent operation that touches contract state, and the contract's storage staking balance grows monotonically, reducing the liquid balance available for other operations.

---

### Likelihood Explanation

Bitcoin and its supported variants (Litecoin, Dogecoin, Zcash) produce natural short forks continuously. The relayer is explicitly designed to detect and submit competing fork chains. Every such submission that does not win the chainwork comparison triggers `store_fork_header` and permanently orphans those entries. This is a routine production event, not an edge case.

---

### Recommendation

Add a function — callable by the relayer or a privileged role — that accepts a list of fork-chain block hashes, verifies they are **not** present in `mainchain_header_to_height`, removes them from `headers_pool`, and returns the freed storage deposit to the caller. Alternatively, extend `run_mainchain_gc` to track and periodically evict fork headers older than a configurable age threshold, refunding the associated storage deposit to the original submitter.

---

### Proof of Concept

1. Initialize the contract with a genesis block.
2. Submit a valid mainchain header `M1` extending genesis. `store_block_header` inserts `M1` into all three maps.
3. Submit a competing fork header `F1` also extending genesis but with lower chainwork than `M1`. `submit_block_header_inner` takes the `else` branch, calls `store_fork_header`, inserting `F1` into `headers_pool` only. The caller pays the storage deposit for `F1`.
4. Call `run_mainchain_gc` with any `batch_size`. The GC loop iterates over `mainchain_height_to_header`, which contains only `M1`-series entries. `F1` is never visited.
5. Inspect `headers_pool`: `F1` is still present. The storage deposit paid in step 3 is permanently locked. No callable function exists to remove `F1` or recover the deposit. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L173-197)
```rust
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );

        let refund = amount.saturating_sub(required_deposit);
        if refund > NearToken::from_near(0) {
            Promise::new(env::predecessor_account_id())
                .transfer(refund)
                .into()
        } else {
            PromiseOrValue::Value(())
        }
```

**File:** contract/src/lib.rs (L377-416)
```rust
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
        let initial_blockheader = self
            .headers_pool
            .get(&self.mainchain_initial_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let tip_blockheader = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let amount_of_headers_we_store =
            tip_blockheader.block_height - initial_blockheader.block_height + 1;

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
    }
```

**File:** contract/src/lib.rs (L560-566)
```rust
            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L616-636)
```rust
        while !self
            .mainchain_header_to_height
            .contains_key(&fork_header_cursor.block_hash)
        {
            let prev_block_hash = fork_header_cursor.block_header.prev_block_hash;
            let current_block_hash = fork_header_cursor.block_hash;
            let current_height = fork_header_cursor.block_height;

            // Inserting the fork block into the main chain, if some mainchain block is occupying
            // this height let's save its hashcode
            let main_chain_block = self
                .mainchain_height_to_header
                .insert(&current_height, &current_block_hash);
            self.mainchain_header_to_height
                .insert(&current_block_hash, &current_height);

            // If we found a mainchain block at the current height than remove this block from the
            // header pool and from the header -> height map
            if let Some(current_main_chain_blockhash) = main_chain_block {
                self.remove_block_header(&current_main_chain_blockhash);
            }
```

**File:** contract/src/lib.rs (L664-667)
```rust
    /// Stores and handles fork submissions
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
    }
```

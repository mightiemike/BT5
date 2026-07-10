### Title
`mainchain_initial_blockhash` Not Updated During Chain Reorganization Causes Permanent Contract Bricking — (`contract/src/lib.rs`)

---

### Summary

During `reorg_chain`, when a fork replaces the block at the `mainchain_initial_blockhash` height, `remove_block_header` evicts the old initial block from `headers_pool`, but `mainchain_initial_blockhash` is never updated to reflect the new fork block at that height. After the reorg, `mainchain_initial_blockhash` holds a stale hash that no longer exists in `headers_pool`. Every subsequent call to `run_mainchain_gc` — which is called unconditionally from `submit_blocks` — panics immediately, permanently bricking block submission.

---

### Finding Description

`BtcLightClient` maintains two coupled pieces of state:

- `mainchain_initial_blockhash` — the hash of the oldest mainchain block currently stored.
- `headers_pool` — the map from block hash → `ExtendedHeader` for all stored blocks.

These two must remain in sync: `mainchain_initial_blockhash` must always be a key present in `headers_pool`.

In `reorg_chain` (lines 575–647 of `contract/src/lib.rs`), the while loop walks backward from the fork tip to the common ancestor, replacing each mainchain block with the corresponding fork block:

```rust
let main_chain_block = self
    .mainchain_height_to_header
    .insert(&current_height, &current_block_hash);   // insert fork block
self.mainchain_header_to_height
    .insert(&current_block_hash, &current_height);

if let Some(current_main_chain_blockhash) = main_chain_block {
    self.remove_block_header(&current_main_chain_blockhash);  // evicts old block
}
```

`remove_block_header` (lines 659–662) removes the displaced mainchain block from both `mainchain_header_to_height` and `headers_pool`:

```rust
fn remove_block_header(&mut self, header_block_hash: &H256) {
    self.mainchain_header_to_height.remove(header_block_hash);
    self.headers_pool.remove(header_block_hash);
}
```

When the common ancestor of the fork is at a height **below** `mainchain_initial_blockhash`, the loop processes the block at `mainchain_initial_blockhash`'s height, evicting the old initial block from `headers_pool`. However, `mainchain_initial_blockhash` is **never updated** anywhere in `reorg_chain`. After the function returns, `mainchain_initial_blockhash` holds a hash that no longer exists in `headers_pool`.

`run_mainchain_gc` (lines 377–416) reads `mainchain_initial_blockhash` unconditionally at its very first line:

```rust
let initial_blockheader = self
    .headers_pool
    .get(&self.mainchain_initial_blockhash)
    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
```

Since `submit_blocks` (lines 169–198) always calls `run_mainchain_gc` after processing headers:

```rust
self.run_mainchain_gc(num_of_headers);
```

every subsequent call to `submit_blocks` panics with `ERR_KEY_NOT_EXIST`. `get_mainchain_size` (lines 222–232) and `get_last_n_blocks_hashes` (lines 237–261) suffer the same panic for the same reason.

---

### Impact Explanation

After a reorg where the fork's common ancestor is below `mainchain_initial_blockhash`:

1. `submit_blocks` permanently panics → no new Bitcoin headers can be submitted.
2. `get_mainchain_size` and `get_last_n_blocks_hashes` permanently panic.
3. The light client's chain state becomes permanently frozen at the post-reorg tip.
4. Any downstream NEAR contract relying on `verify_transaction_inclusion` for new blocks will eventually fail as the stored chain becomes stale and confirmations can no longer be accumulated.
5. Recovery requires a privileged contract upgrade/redeployment.

This is a direct analog to M-20: two values that must remain coupled (`mainchain_initial_blockhash` ↔ `headers_pool`) diverge due to an external operation (reorg), causing all subsequent operations that depend on their consistency to fail permanently.

---

### Likelihood Explanation

The trigger requires a Bitcoin fork whose common ancestor with the current mainchain is at a height below `mainchain_initial_blockhash`. This is realistic because:

- `mainchain_initial_blockhash` advances over time as GC runs (every `submit_blocks` call invokes GC). After the contract has been running for months, `mainchain_initial_blockhash` may be thousands of blocks above the genesis height.
- Bitcoin does experience forks (including deep ones during network partitions or selfish-mining attacks). A fork starting below the GC boundary is plausible on a long-running deployment.
- The relayer submitting the fork blocks is doing so legitimately — it is simply relaying valid Bitcoin chain data. No malicious intent is required; the bug is triggered by correct relayer behavior on a valid (if unusual) Bitcoin chain event.
- The `submit_blocks` entry point is accessible to any staked trusted relayer, which is a production role, not a privileged admin.

---

### Recommendation

In `reorg_chain`, after the while loop completes, check whether the block previously recorded as `mainchain_initial_blockhash` was displaced. If so, update `mainchain_initial_blockhash` to the hash of the fork block now occupying that height:

```rust
// After the while loop in reorg_chain:
let initial_height = self
    .headers_pool
    .get(&self.mainchain_initial_blockhash)
    .map(|h| h.block_height);

if initial_height.is_none() {
    // The initial block was displaced; find the new block at that height.
    // Retrieve the height from the old hash before it was removed, or
    // track it explicitly during the loop.
    // Update mainchain_initial_blockhash to the fork block now at that height.
    self.mainchain_initial_blockhash = self
        .mainchain_height_to_header
        .get(&displaced_initial_height)
        .unwrap_or_else(|| env::panic_str("initial height must have a block after reorg"));
}
```

Alternatively, track the height of `mainchain_initial_blockhash` explicitly during the loop and update the field whenever the block at that height is replaced.

---

### Proof of Concept

**Setup:**
- Contract initialized with genesis at height 0, `gc_threshold = 100`.
- Relayer submits blocks 0–200. GC runs; `mainchain_initial_blockhash` advances to height 100.
- Mainchain tip is at height 200.

**Attack / Trigger:**
1. A Bitcoin fork exists starting at height 95 (below `mainchain_initial_blockhash` = 100). The fork tip is at height 210 with higher cumulative chainwork.
2. The relayer submits fork blocks 96–210 via `submit_blocks`. Each is stored in `headers_pool` via `store_fork_header`.
3. When block 210 is submitted, `submit_block_header_inner` detects higher chainwork and calls `reorg_chain(fork_tip_at_210, 200)`.
4. Inside `reorg_chain`, the while loop walks back: 210 → 209 → … → 101 → **100**. At height 100, `mainchain_height_to_header.insert(100, fork_block_100_hash)` returns `Some(old_initial_hash)`. `remove_block_header(old_initial_hash)` removes the old block from `headers_pool`. `mainchain_initial_blockhash` is **not updated**.
5. The loop finds that the fork block at height 95 is already in `mainchain_header_to_height` (it's the common ancestor) and terminates.
6. `mainchain_tip_blockhash` = fork tip at 210. `mainchain_initial_blockhash` = stale hash of the evicted block at height 100.

**Result:**
- `run_mainchain_gc` is called next (still inside the same `submit_blocks` call, line 181).
- `self.headers_pool.get(&self.mainchain_initial_blockhash)` returns `None`.
- Contract panics: `ERR_KEY_NOT_EXIST`.
- All future `submit_blocks` calls panic at the same point. The contract is permanently bricked. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L169-198)
```rust
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
    }
```

**File:** contract/src/lib.rs (L222-232)
```rust
    pub fn get_mainchain_size(&self) -> u64 {
        let tail = self
            .headers_pool
            .get(&self.mainchain_initial_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let tip = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        tip.block_height - tail.block_height + 1
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

**File:** contract/src/lib.rs (L575-647)
```rust
    fn reorg_chain(&mut self, fork_tip_header: ExtendedHeader, last_main_chain_block_height: u64) {
        let fork_tip_height = fork_tip_header.block_height;
        if last_main_chain_block_height > fork_tip_height {
            // If we see that main chain is longer than fork we first garbage collect
            // outstanding main chain blocks:
            //
            //      [m1] - [m2] - [m3] - [m4] <- We should remove [m4]
            //     /
            // [m0]
            //     \
            //      [f1] - [f2] - [f3]
            for height in (fork_tip_height + 1)..=last_main_chain_block_height {
                let current_main_chain_blockhash = self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str("cannot get a block"));
                self.remove_block_header(&current_main_chain_blockhash);
                self.mainchain_height_to_header.remove(&height);
            }
        }

        // Now we are in a situation where mainchain is equivalent to fork size:
        //
        //      [m1] - [m2] - [m3] - [m4] <- main tip
        //     /
        // [m0]
        //     \
        //      [f1] - [f2] - [f3] - [f4] <- fork tip
        //
        //
        // Or in a situation where it is shorter:
        //
        //      [m1] - [m2] - [m3] <- main tip
        //     /
        // [m0]
        //     \
        //      [f1] - [f2] - [f3] - [f4] <- fork tip

        let fork_tip_hash = fork_tip_header.block_hash.clone();
        let mut fork_header_cursor = fork_tip_header;

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

            // Switch iterator cursor to the previous block in fork
            fork_header_cursor = self
                .headers_pool
                .get(&prev_block_hash)
                .unwrap_or_else(|| env::panic_str("previous fork block should be there"));
        }

        // Updating tip of the new main chain
        self.mainchain_tip_blockhash = fork_tip_hash;
    }
```

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```

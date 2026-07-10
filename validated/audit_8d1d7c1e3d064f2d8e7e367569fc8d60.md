### Title
Superseded Fork Headers Are Never Garbage-Collected, Permanently Locking NEAR Storage — (`contract/src/lib.rs`)

---

### Summary

`store_fork_header` inserts fork block headers exclusively into `headers_pool`. The only GC path, `run_mainchain_gc`, iterates solely over `mainchain_height_to_header` to discover blocks to remove. Fork headers have no entry in `mainchain_height_to_header`, making them permanently invisible to GC. Once a fork is superseded — either because the mainchain outpaces it or a different fork wins a reorg — its headers remain locked in `headers_pool` indefinitely with no mechanism to free them.

This is a direct analog to M-03: just as out-of-money put options continue to lock Atlantic Vault collateral after the underlying bond is redeemed (with no settlement path), superseded fork headers continue to lock NEAR storage staking tokens after the fork is abandoned (with no GC path).

---

### Finding Description

**`store_fork_header` writes only to `headers_pool`:** [1](#0-0) 

Fork headers are never inserted into `mainchain_height_to_header` or `mainchain_header_to_height`.

**`run_mainchain_gc` discovers blocks exclusively via `mainchain_height_to_header`:** [2](#0-1) 

The GC loop iterates `start_removal_height..end_removal_height` using `mainchain_height_to_header.get(&height)`. Any block absent from that map — i.e., every fork header — is never visited and never removed.

**`remove_block_header` also only removes from `mainchain_header_to_height` and `headers_pool`:** [3](#0-2) 

Even if called, it cannot reach a fork header that was never registered in `mainchain_header_to_height`.

**`submit_block_header_inner` is the dispatch point:** [4](#0-3) 

Any block whose parent is not the current mainchain tip is routed to `store_fork_header`. If its chainwork does not exceed the mainchain tip's chainwork, `reorg_chain` is never called and the header sits in `headers_pool` forever.

**During a reorg, only the *displaced* mainchain blocks are cleaned up — not the losing fork's blocks:** [5](#0-4) 

`remove_block_header` is called only for the old mainchain block that was displaced at each height. Blocks from a previously submitted losing fork that share no height with the winning fork are never touched.

---

### Impact Explanation

NEAR storage staking works by locking NEAR tokens proportional to bytes used. `submit_blocks` measures `diff_storage_usage` and charges the relayer a deposit for new storage: [6](#0-5) 

The relayer pays for fork header storage at submission time. Because no GC path exists for fork headers, that storage — and the staked NEAR tokens backing it — is permanently locked. Over time, as the Bitcoin network produces natural forks (stale blocks, competing miners), `headers_pool` grows without bound. The contract's `mainchain_initial_blockhash` pointer advances, but the orphaned fork entries in `headers_pool` are never reclaimed.

Two concrete consequences mirror M-03 exactly:
1. **Locked storage**: NEAR staking tokens remain locked for every superseded fork header, with no release mechanism.
2. **Unbounded pool growth**: `headers_pool` accumulates all fork headers ever submitted, regardless of whether the fork was abandoned seconds after submission.

---

### Likelihood Explanation

Bitcoin-family networks produce stale/orphan blocks continuously. The relayer's synchronizer submits headers as they arrive; any block that arrives before its sibling becomes a fork submission. On mainnet Bitcoin, stale blocks occur multiple times per week. On Dogecoin (1-minute blocks), the rate is higher. Every such submission adds a permanently unrecoverable entry to `headers_pool`. This is not a theoretical edge case — it is the normal operating condition of any live deployment.

---

### Recommendation

Introduce a separate GC path for fork headers. One approach: maintain a secondary index (e.g., `fork_headers_by_height: LookupMap<u64, Vec<H256>>`) populated by `store_fork_header`. Extend `run_mainchain_gc` to also sweep fork entries at heights that have fallen below `mainchain_initial_blockhash`. Alternatively, remove fork headers from `headers_pool` immediately when a reorg completes and the fork is definitively superseded, by walking the losing fork chain and calling `headers_pool.remove` for each of its blocks.

---

### Proof of Concept

1. Contract is initialized with genesis block at height 0.
2. Relayer submits mainchain block at height 1 (hash `M1`). `store_block_header` writes `M1` into all three maps.
3. Relayer submits a competing fork block at height 1 (hash `F1`, same parent, lower chainwork). `store_fork_header` writes `F1` into `headers_pool` only. `reorg_chain` is not called.
4. Relayer continues submitting mainchain blocks M2, M3, … M_gc_threshold+1.
5. `run_mainchain_gc` fires. It iterates `mainchain_height_to_header` from height 0 upward, removes `M1` (and its storage), advances `mainchain_initial_blockhash` to height 1.
6. `F1` is still present in `headers_pool`. It has no entry in `mainchain_height_to_header`, so GC never visits it. The NEAR storage staking tokens for `F1` remain locked permanently.
7. Repeating steps 3–6 for every natural stale block causes `headers_pool` to grow without bound, with no recovery path. [7](#0-6) [1](#0-0)

### Citations

**File:** contract/src/lib.rs (L182-188)
```rust
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );
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

**File:** contract/src/lib.rs (L549-567)
```rust
        } else {
            log!("Block {}: saving to fork", current_header.block_hash);
            // Fork submission
            let main_chain_tip_header = self
                .headers_pool
                .get(&self.mainchain_tip_blockhash)
                .unwrap_or_else(|| env::panic_str("tip should be in a header pool"));

            let last_main_chain_block_height = main_chain_tip_header.block_height;
            let total_main_chain_chainwork = main_chain_tip_header.chain_work;

            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
```

**File:** contract/src/lib.rs (L634-636)
```rust
            if let Some(current_main_chain_blockhash) = main_chain_block {
                self.remove_block_header(&current_main_chain_blockhash);
            }
```

**File:** contract/src/lib.rs (L658-662)
```rust
    /// Remove block header and meta information
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```

**File:** contract/src/lib.rs (L664-667)
```rust
    /// Stores and handles fork submissions
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
    }
```

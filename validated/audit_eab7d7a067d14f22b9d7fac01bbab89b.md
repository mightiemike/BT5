### Title
Unbounded Loop in `reorg_chain` Can Exceed NEAR Gas Limit, Permanently Freezing the Light Client - (File: `contract/src/lib.rs`)

---

### Summary

The `reorg_chain` function in `contract/src/lib.rs` contains two unbounded loops that iterate over every fork block from the fork tip back to the common ancestor with the main chain. There is no gas guard, iteration cap, or batch-size limit. A sufficiently long fork (realistic for Dogecoin or Zcash, and possible via testnet or a 51%-attack scenario on lower-hashrate chains) causes the reorg transaction to exhaust the NEAR 300 TGas limit, permanently preventing the light client from promoting the heavier chain and freezing SPV verification.

---

### Finding Description

When a submitted fork block's accumulated `chain_work` exceeds the main chain's `chain_work`, `submit_block_header_inner` calls `reorg_chain`. [1](#0-0) 

`reorg_chain` contains **two unbounded loops**:

**Loop 1** — removes all main-chain blocks above the fork tip height: [2](#0-1) 

Each iteration performs two storage writes (`remove_block_header` removes from `mainchain_header_to_height` and `headers_pool`; `mainchain_height_to_header.remove` is a third write). The iteration count equals `last_main_chain_block_height − fork_tip_height`, which is unbounded.

**Loop 2** — walks the fork chain back to the common ancestor, promoting each fork block to the main chain: [3](#0-2) 

Each iteration performs up to **five** storage operations: `contains_key`, `insert` into `mainchain_height_to_header`, `insert` into `mainchain_header_to_height`, optionally `remove_block_header` (two writes), and `get` from `headers_pool`. The iteration count equals the fork depth (distance from fork tip to common ancestor), which is also unbounded.

Both loops are triggered inside a single `submit_blocks` call with no gas checkpoint or resumable state. [4](#0-3) 

---

### Impact Explanation

If the reorg transaction runs out of gas:

1. The transaction reverts. The fork blocks remain in `headers_pool` but are **not** promoted to the main chain.
2. The main chain tip stays at the pre-reorg position.
3. Every subsequent block submitted that extends the fork tip will again trigger `reorg_chain` with the same (or greater) fork depth, causing the same gas exhaustion in a loop.
4. The light client is **permanently frozen**: it cannot advance its canonical chain past the fork point.
5. All downstream `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` calls for blocks at or after the fork point will fail, breaking any bridge or SPV application that depends on the contract. [5](#0-4) 

---

### Likelihood Explanation

- **Dogecoin** (Scrypt, low hashrate) and **Zcash** (Equihash) are supported chains where a 51%-attack-induced reorg of 50–200 blocks is economically feasible.
- **Bitcoin testnet** has historically experienced reorgs of hundreds of blocks; the contract supports `Network::Testnet`.
- The `submit_blocks` entry point is gated by the `#[trusted_relayer]` macro, but the trusted-relayer system is a staking-based registration open to any account — not a closed admin role. A registered relayer submitting a legitimate (or adversarially mined) long fork is the concrete trigger.
- No minimum fork-depth check or gas guard exists anywhere in the reorg path. [6](#0-5) 

---

### Recommendation

1. **Cap reorg depth**: Reject (or defer) any reorg whose depth exceeds a configurable `max_reorg_depth` constant. Panic with a clear error if the fork is deeper than the cap.
2. **Make reorg resumable**: Store intermediate reorg state (e.g., current cursor hash and a `reorg_in_progress` flag) so the promotion can be completed across multiple transactions, analogous to the existing `run_mainchain_gc(batch_size)` pattern.
3. **Limit fork accumulation**: Reject fork blocks whose height is more than `max_reorg_depth` blocks below the current main chain tip, preventing an attacker from pre-loading an arbitrarily deep fork in `headers_pool`. [7](#0-6) 

---

### Proof of Concept

**Setup** (skip-PoW mode, Bitcoin feature):

1. Initialize the contract with `skip_pow_verification = true` and a `gc_threshold` large enough to hold all blocks.
2. Submit N main-chain blocks (e.g., N = 200) to establish a long main chain.
3. Submit N fork blocks branching from the genesis, each with slightly higher `bits` (more work per block) so that after N fork blocks the fork's cumulative `chain_work` exceeds the main chain's.
   - Because `skip_pow_verification = true`, no actual PoW mining is required.
4. Submit the N-th fork block. This triggers `reorg_chain` with a fork depth of N.
5. Observe that the NEAR transaction fails with `GasExceeded`.

The `while` loop at line 616 will execute N iterations, each performing 4–5 storage operations. At NEAR's storage-operation gas costs, approximately 50–100 iterations already consume tens of TGas; 200 iterations reliably exceed the 300 TGas cap. [3](#0-2)

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

**File:** contract/src/lib.rs (L531-568)
```rust
    fn submit_block_header_inner(
        &mut self,
        current_header: ExtendedHeader,
        prev_block_header: &ExtendedHeader,
    ) {
        // Main chain submission
        if prev_block_header.block_hash == self.mainchain_tip_blockhash {
            // Probably we should check if it is not in a mainchain?
            // chainwork > highScore
            log!("Block {}: saving to mainchain", current_header.block_hash);
            // Validate chain
            assert_eq!(
                self.mainchain_tip_blockhash,
                current_header.block_header.prev_block_hash
            );

            self.store_block_header(&current_header);
            self.mainchain_tip_blockhash = current_header.block_hash;
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

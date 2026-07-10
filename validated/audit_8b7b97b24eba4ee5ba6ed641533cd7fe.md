### Title
Unbounded Loop in `reorg_chain` Can Exhaust NEAR Gas Limit During Long Fork Promotion — (File: `contract/src/lib.rs`)

---

### Summary

The `reorg_chain` function contains two unbounded loops that iterate over every block in a reorganization. For a sufficiently long fork, these loops exhaust the NEAR transaction gas limit (300 TGas) in a single call, permanently preventing the contract from promoting the heavier fork and leaving the light client tracking a stale, lower-chainwork chain.

---

### Finding Description

`reorg_chain` is invoked from `submit_block_header_inner` whenever a fork's cumulative chainwork exceeds the current main chain's chainwork. [1](#0-0) 

It contains two loops with no gas-budget check or early-exit:

**Loop 1 — remove orphaned main-chain blocks above the fork tip:** [2](#0-1) 

Each iteration performs one `LookupMap::get` (storage read) and two `LookupMap::remove` calls (storage writes).

**Loop 2 — walk the fork chain back to the common ancestor and promote each block:** [3](#0-2) 

Each iteration performs two `LookupMap::get`/`contains_key` calls (storage reads) and three-to-four `LookupMap::insert`/`remove` calls (storage writes).

Neither loop is bounded by a gas guard. The total iteration count across both loops equals the reorganization depth — the number of blocks from the common ancestor to the fork tip. In NEAR Protocol, each storage write costs ~0.115 TGas and each storage read costs ~0.0055 TGas against a hard per-transaction ceiling of 300 TGas. At roughly 0.47 TGas per iteration of Loop 2 alone, the budget is exhausted after approximately 600 iterations.

The entry path is `submit_blocks` → `submit_block_header` → `submit_block_header_inner` → `reorg_chain`. [4](#0-3) 

`submit_blocks` is gated by `#[trusted_relayer]`, which the codebase treats as a staking-based relayer role (the prompt explicitly lists "relayer-path user supplying adversarial chain data" as a valid entry point). The relayer service itself already acknowledges gas-exceeded failures and implements adaptive batching: [5](#0-4) 

However, adaptive batching only reduces the number of headers per transaction. It cannot help when a **single** header triggers a reorg of hundreds of blocks — the gas is consumed inside one `submit_block_header` call, not spread across the batch.

---

### Impact Explanation

When the gas limit is hit inside `reorg_chain`, the entire `submit_blocks` transaction reverts. The contract state is unchanged: it continues to track the old, lower-chainwork chain. The relayer will retry with a smaller batch, but even a batch of one header fails identically if the reorg depth exceeds ~600 blocks. The light client becomes permanently stuck on the stale chain. Any downstream consumer calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against the stale chain will receive incorrect confirmation results for transactions that are only confirmed on the true main chain. [6](#0-5) 

---

### Likelihood Explanation

Bitcoin mainnet reorganizations beyond 6 blocks are rare. However, the contract is compiled for Dogecoin, Litecoin, and Zcash as well, chains that have historically experienced deeper reorganizations. Additionally, a relayer that has accumulated many fork headers over time (each individually valid and accepted by the contract) can trigger the condition by submitting the final header that tips chainwork in the fork's favor. The relayer role, while requiring a stake, is not restricted to a single privileged key.

---

### Recommendation

Refactor `reorg_chain` to process the reorganization incrementally across multiple transactions, analogous to the fix described in the external report. Concretely:

1. Store a `pending_reorg` field in contract state recording the fork tip hash and the current cursor position.
2. Expose a separate `continue_reorg(batch_size: u64)` method that advances the cursor by at most `batch_size` blocks per call.
3. Block new header submissions until any pending reorg is fully resolved.
4. Alternatively, enforce a hard cap on the maximum reorg depth the contract will process in a single call and reject fork promotions that exceed it.

---

### Proof of Concept

1. A trusted relayer submits N fork headers (N ≈ 700) in incremental batches, each building on the previous fork block. Each header is accepted and stored in `headers_pool` as a fork block via `store_fork_header`. [7](#0-6) 

2. The relayer submits one final fork header whose cumulative `chain_work` exceeds `total_main_chain_chainwork`. [8](#0-7) 

3. `reorg_chain` is called. The `while` loop at line 616 begins traversing the 700-block fork chain, performing ~4 storage writes per iteration.

4. After ~600 iterations (~276 TGas in writes alone), the 300 TGas ceiling is reached. The transaction panics with gas exhaustion and reverts.

5. The contract remains on the old main chain. Every subsequent attempt to submit the same reorg-triggering header reproduces the same failure. The light client is permanently unable to track the true Bitcoin tip. [9](#0-8)

### Citations

**File:** contract/src/lib.rs (L177-179)
```rust
        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }
```

**File:** contract/src/lib.rs (L288-323)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
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

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L586-593)
```rust
            for height in (fork_tip_height + 1)..=last_main_chain_block_height {
                let current_main_chain_blockhash = self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str("cannot get a block"));
                self.remove_block_header(&current_main_chain_blockhash);
                self.mainchain_height_to_header.remove(&height);
            }
```

**File:** contract/src/lib.rs (L616-646)
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

            // Switch iterator cursor to the previous block in fork
            fork_header_cursor = self
                .headers_pool
                .get(&prev_block_hash)
                .unwrap_or_else(|| env::panic_str("previous fork block should be there"));
        }

        // Updating tip of the new main chain
        self.mainchain_tip_blockhash = fork_tip_hash;
```

**File:** contract/src/lib.rs (L664-667)
```rust
    /// Stores and handles fork submissions
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
    }
```

**File:** relayer/src/main.rs (L192-203)
```rust
                Ok(Err(CustomError::GasExceeded)) => {
                    warn!(target: "relay", "Gas exceeded for blocks [{} - {}], reducing batch size",
                        tx.first_block_height, tx.last_block_height);
                    {
                        let mut sizer = cloned_self.batch_sizer.lock().await;
                        sizer.on_gas_exceeded();
                    }
                    let Ok(last_block_height) = cloned_self.get_last_correct_block_height().await else {
                        return Err("Error on get_last_block_height".to_string());
                    };
                    first_block_height_to_submit.store(last_block_height + 1, std::sync::atomic::Ordering::SeqCst);
                }
```

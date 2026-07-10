### Title
Unbounded Loop in `reorg_chain` Can Exhaust Gas and Permanently Corrupt Canonical Chain State - (File: contract/src/lib.rs)

### Summary
`reorg_chain()` contains two unbounded loops whose iteration count is proportional to the fork length. If a fork is long enough that the reorg transaction exceeds NEAR's gas limit, the transaction reverts, but the fork blocks already stored in `headers_pool` remain. The reorg-triggering block can never be successfully submitted because every attempt re-triggers the same unbounded loop. The canonical chain tip is permanently stuck, breaking all SPV verification.

### Finding Description
`submit_blocks` → `submit_block_header` → `submit_block_header_inner` → `reorg_chain` is the call chain. Inside `reorg_chain`, two loops run without any iteration cap or gas guard:

**Loop 1** (demoting excess main-chain blocks): [1](#0-0) 

This iterates `last_main_chain_block_height − fork_tip_height` times, performing two storage operations per iteration.

**Loop 2** (promoting fork blocks to main chain): [2](#0-1) 

This walks from the fork tip back to the common ancestor, performing three to four storage operations per iteration. The number of iterations equals the fork depth (number of fork blocks that diverge from the common ancestor).

Neither loop has a bound, a gas check, or a resumable checkpoint. The contract has no on-chain limit on fork length. The relayer's off-chain `max_fork_len: 500` config is not enforced by the contract. [3](#0-2) 

The trigger is the final `submit_blocks` call that pushes the fork's `chain_work` above the main chain's `chain_work`: [4](#0-3) 

All prior fork blocks were stored in `headers_pool` across previous successful transactions. When the reorg-triggering block is submitted and the transaction runs out of gas, NEAR reverts the entire transaction. The fork tip block is not stored. On retry, the same block is submitted, the same reorg is triggered, and the same gas exhaustion occurs — an unbreakable cycle.

### Impact Explanation
The canonical chain tip (`mainchain_tip_blockhash`) is permanently stuck at the pre-reorg position. The corrupted state is:
- `mainchain_tip_blockhash` points to the old (lower-work) chain tip
- `mainchain_height_to_header` / `mainchain_header_to_height` reflect the old chain
- Fork blocks for the true higher-work chain are orphaned in `headers_pool`

All calls to `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` for blocks on the true chain will fail with "block does not belong to the current main chain," permanently breaking SPV verification for any downstream NEAR contract relying on this light client. [5](#0-4) 

**Impact: High** — permanent corruption of the canonical chain mapping and all dependent SPV proof results.

### Likelihood Explanation
**Likelihood: Low** — `submit_blocks` is gated by `#[trusted_relayer]`, limiting callers to staked relayers. [6](#0-5) 

However, the trigger does not require adversarial intent. A legitimate relayer faithfully relaying a real Bitcoin reorganization of sufficient depth (e.g., a few hundred blocks) would trigger the same gas exhaustion. NEAR's maximum gas per transaction is 300 Tgas. Each loop iteration performs multiple `LookupMap` storage reads and writes; at realistic NEAR storage costs, a fork of a few hundred blocks is sufficient to exhaust the gas budget. The `gc_threshold` default of 52,704 blocks means forks up to that depth can be stored, making the upper bound on loop iterations extremely large. [7](#0-6) 

### Recommendation
Impose an on-chain cap on the maximum fork length that `reorg_chain` will process in a single call. If the fork exceeds the cap, panic with a descriptive error before any state is mutated, so the relayer can take corrective action (e.g., submit the fork incrementally or raise the gas budget). Alternatively, make the reorg resumable across multiple transactions by checkpointing progress in contract state.

### Proof of Concept
1. Deploy the contract with `gc_threshold = 52704`.
2. Submit N main-chain blocks (e.g., N = 500) via `submit_blocks`, advancing `mainchain_tip_blockhash` to height N.
3. Submit N+1 fork blocks (all branching from height 0) via separate `submit_blocks` calls, each storing one fork block in `headers_pool`. Each call succeeds individually.
4. Submit the (N+2)-th fork block whose cumulative `chain_work` exceeds the main chain's. This triggers `reorg_chain`, which must iterate through all N+1 fork blocks in Loop 2 plus N blocks in Loop 1 — totaling ~2N+1 storage-heavy iterations in a single transaction.
5. The transaction exceeds NEAR's gas limit and reverts. The fork tip block is not stored.
6. Retry step 4 indefinitely — every attempt produces the same gas exhaustion. `mainchain_tip_blockhash` is permanently stuck at height N, and `verify_transaction_inclusion` for any block on the fork chain permanently returns a panic.

### Citations

**File:** contract/src/lib.rs (L131-131)
```rust
    /// * `gc_threshold = 52704`: This is the approximate number of blocks generated in a year.
```

**File:** contract/src/lib.rs (L166-172)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
```

**File:** contract/src/lib.rs (L299-302)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

```

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L574-647)
```rust
    /// The most expensive operation which reorganizes the chain, based on fork weight
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

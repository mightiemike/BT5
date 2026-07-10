### Title
Unbounded `while` Loop in `reorg_chain` Allows Gas Exhaustion to Freeze Fork-Choice State — (`File: contract/src/lib.rs`)

### Summary
`reorg_chain` contains an unbounded `while` loop that walks every fork block back to the common ancestor in a single NEAR transaction. A trusted relayer can build an arbitrarily long fork over many cheap transactions, then submit the tip block that triggers the reorg. The single reorg transaction iterates over all accumulated fork blocks, exhausts NEAR's 300 TGas limit, and fails. The contract's canonical chain pointer is never updated, permanently leaving `mainchain_tip_blockhash` pointing to the lighter chain even though a heavier fork exists in storage.

### Finding Description

`submit_block_header_inner` calls `reorg_chain` whenever a newly submitted fork block's `chain_work` exceeds the current mainchain tip's `chain_work`: [1](#0-0) 

Inside `reorg_chain`, after optionally trimming excess mainchain blocks, the function enters an unbounded `while` loop that walks backward through every fork block until it reaches the common ancestor: [2](#0-1) 

Each iteration performs multiple `LookupMap` reads and writes (`contains_key`, `insert` into two maps, `remove_block_header`). There is no cap on the number of iterations — the loop runs exactly `fork_length` times, where `fork_length` is the number of fork blocks between the common ancestor and the fork tip.

The contract imposes no upper bound on fork length. The only limit is the relayer-side configuration value `max_fork_len = 500`: [3](#0-2) 

This is a relayer-side advisory value, not enforced by the contract. Any caller of `submit_blocks` can submit fork blocks one at a time over many transactions (each individually cheap), accumulating an arbitrarily long fork in `headers_pool`. When the final block tips the chainwork balance, the single reorg transaction must process all accumulated fork blocks in one shot.

### Impact Explanation

When the reorg transaction exceeds NEAR's 300 TGas limit and fails:

- `mainchain_tip_blockhash` is never updated to the fork tip.
- `mainchain_height_to_header` / `mainchain_header_to_height` are not updated.
- The fork blocks remain in `headers_pool` but are never promoted.

The contract's canonical chain now permanently points to the lighter chain. All subsequent calls to `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` check confirmations and block membership against the wrong chain: [4](#0-3) 

Downstream contracts consuming SPV proofs receive incorrect verification results — transactions on the heavier (true) chain are rejected, and transactions on the stale lighter chain may be accepted. This is a fork-choice corruption, not merely a performance issue.

### Likelihood Explanation

A trusted relayer is an economic participant who has staked tokens; registration is not restricted to a privileged admin. A malicious or compromised relayer can execute this attack by:

1. Submitting N fork blocks over N separate transactions (each well within gas limits, each storing one header in `headers_pool`).
2. Submitting the (N+1)th fork block whose cumulative `chain_work` exceeds the mainchain tip.
3. The contract calls `reorg_chain`, which loops N times in one transaction and hits the gas limit.

For Bitcoin, a fork of ~200–300 blocks (each iteration doing 4–5 storage ops) is sufficient to exhaust 300 TGas. The relayer config's `max_fork_len = 500` shows the system already anticipates forks of this length. The attack requires no cryptographic break — only the ability to submit valid (or PoW-skipped) headers.

### Recommendation

Enforce a hard cap on fork length inside the contract. Before entering the `while` loop in `reorg_chain`, compute the fork depth and panic (or return an error) if it exceeds a configurable `max_reorg_depth` parameter stored in contract state. This mirrors the `batch_size` pattern already used in `run_mainchain_gc`: [5](#0-4) 

Alternatively, split the reorg into multiple transactions by storing intermediate reorg state, similar to how `run_mainchain_gc` accepts a `batch_size` argument to bound per-call work.

### Proof of Concept

1. Deploy the contract with `skip_pow_verification = true` and `gc_threshold = 100000`.
2. Submit a mainchain of height H with chainwork W.
3. Submit 300 fork blocks branching from height H−1, each with `bits` set to give slightly higher per-block work. Each `submit_blocks` call stores one block in `headers_pool` cheaply (no reorg triggered yet, since cumulative fork work < W).
4. Submit the 301st fork block, whose cumulative `chain_work` now exceeds W. This triggers `submit_block_header_inner` → `reorg_chain`.
5. `reorg_chain`'s `while` loop iterates 301 times, each performing 4–5 `LookupMap` operations. The transaction runs out of gas and fails.
6. Call `get_last_block_header()` — it still returns the original mainchain tip at height H, not the fork tip at H+300. The heavier chain is silently ignored. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L294-313)
```rust
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
```

**File:** contract/src/lib.rs (L377-393)
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
```

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
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

**File:** relayer/configs/btc_mainnet.toml (L1-1)
```text
max_fork_len = 500
```

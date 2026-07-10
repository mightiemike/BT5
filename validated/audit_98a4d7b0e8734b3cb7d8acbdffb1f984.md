### Title
Unbounded `while` Loop in `reorg_chain` Exhausts Gas, Permanently Freezing Canonical Chain Promotion — (`File: contract/src/lib.rs`)

### Summary
`reorg_chain` walks the entire fork chain back to the common ancestor in a single unbounded `while` loop with no batch mechanism. A fork long enough to exhaust NEAR's per-transaction gas limit (300 TGas) causes every reorg attempt to revert, permanently preventing the fork from being promoted to the main chain. The contract's canonical chain mapping (`mainchain_tip_blockhash`, `mainchain_height_to_header`, `mainchain_header_to_height`) is then permanently desynchronized from the actual Bitcoin canonical chain, corrupting all subsequent SPV proof verification results.

### Finding Description

`submit_blocks` → `submit_block_header` → `submit_block_header_inner` → `reorg_chain` is the call chain. When a submitted fork block's `chain_work` exceeds the main chain's `chain_work`, `reorg_chain` is invoked unconditionally with no bound on the work it must perform in a single transaction. [1](#0-0) 

Inside `reorg_chain`, two loops run without any `batch_size` cap:

**Loop 1** — demotes excess main-chain blocks above the fork tip: [2](#0-1) 

**Loop 2** — walks the entire fork chain back to the common ancestor, performing 4–6 storage operations per iteration (`contains_key`, two `insert`s, one `get`, and up to two `remove`s via `remove_block_header`): [3](#0-2) 

By contrast, `run_mainchain_gc` — the only other loop-heavy function — explicitly accepts a caller-supplied `batch_size` to cap work per call: [4](#0-3) 

No equivalent safety valve exists for `reorg_chain`.

On NEAR, each storage read/write costs gas. With ~6 storage operations per iteration, a fork of a few thousand blocks is sufficient to exhaust the 300 TGas transaction limit. When the transaction reverts, all state changes (including `store_fork_header`) are rolled back. The relayer retries, calls `store_fork_header` again, then `reorg_chain` again — and fails again indefinitely. There is no partial-reorg mechanism to resume from where the previous attempt left off.

### Impact Explanation

The corrupted state is concrete and permanent:

- `mainchain_tip_blockhash` remains pointing to the old (now non-canonical) chain tip.
- `mainchain_height_to_header` and `mainchain_header_to_height` remain indexed to the old chain.
- `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` both resolve the canonical chain through these maps: [5](#0-4) 

After the freeze, the contract accepts SPV proofs for transactions in the old (now non-canonical) chain and rejects proofs for transactions in the actual canonical chain. This is a direct corruption of the contract's core security guarantee.

### Likelihood Explanation

On Bitcoin mainnet, triggering a reorg requires a fork with more cumulative PoW than the main chain, which demands enormous hash power. However:

- The contract also supports Dogecoin, Litecoin, and Zcash — chains with significantly lower difficulty where long forks are more feasible.
- A registered relayer (staking-based, not a privileged admin role — any account can register) who controls sufficient hash power on a lower-difficulty chain can deliberately build and submit a long fork.
- Even without malicious intent, a legitimate long fork on a low-difficulty chain (e.g., a testnet) can trigger this naturally.

The `trusted_relayer` guard requires staking, not a privileged admin grant, so the entry path is economically open. [6](#0-5) 

### Recommendation

Add a `batch_size` parameter to `reorg_chain` (or introduce a separate public `run_reorg(batch_size: u64)` function) that limits the number of fork blocks promoted per transaction, analogous to `run_mainchain_gc`. Store intermediate reorg state (e.g., the current fork cursor hash and the set of already-demoted main-chain blocks) in contract storage so that subsequent calls can resume from where the previous call left off. Only finalize `mainchain_tip_blockhash` once the reorg is fully complete.

### Proof of Concept

1. Deploy the contract on a low-difficulty chain (e.g., Dogecoin testnet) with `skip_pow_verification = false`.
2. Register as a relayer by staking the minimum required amount.
3. Mine a fork of N blocks (N large enough to exhaust 300 TGas in `reorg_chain`) with cumulative chain work exceeding the main chain.
4. Submit the fork blocks one by one via `submit_blocks`; each goes to `store_fork_header` without triggering a reorg.
5. Submit the fork tip block. `submit_block_header_inner` detects `current_header.chain_work > total_main_chain_chainwork` and calls `reorg_chain`.
6. `reorg_chain`'s `while` loop iterates over all N fork blocks, exhausting gas. The transaction reverts.
7. Retry step 5 — same result. The contract is permanently stuck: `mainchain_tip_blockhash` points to the old chain, and `verify_transaction_inclusion_v2` now verifies proofs against the wrong canonical chain. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** contract/src/lib.rs (L299-302)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

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

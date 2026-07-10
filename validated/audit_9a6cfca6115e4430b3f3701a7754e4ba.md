### Title
Fork Headers Stored in `headers_pool` Are Never Garbage-Collected and Cannot Be Removed by Any Privileged Method — (`contract/src/lib.rs`)

### Summary

The `BtcLightClient` contract permanently accumulates fork block headers in `headers_pool` with no removal path. The GC routine only prunes mainchain entries; fork headers written by `store_fork_header` are never touched. No owner-only or pause-gated method exists to evict them. This is a direct structural analog to the RootManager "no way to remove fraudulent roots" finding: once a fraudulent fork header is in the pool it persists indefinitely, and a future reorg can promote it to the canonical chain.

---

### Finding Description

`headers_pool` is declared as storing **all ever-submitted headers, including forks**: [1](#0-0) 

Fork headers are written by `store_fork_header`, which inserts only into `headers_pool` and nowhere else: [2](#0-1) 

The only removal routine is `run_mainchain_gc`. It iterates exclusively over `mainchain_height_to_header` — a map that **never contains fork entries** — and calls `remove_block_header` only for those mainchain hashes: [3](#0-2) 

`remove_block_header` itself removes from `mainchain_header_to_height` and `headers_pool`: [4](#0-3) 

Because fork headers are never inserted into `mainchain_height_to_header`, the GC loop never reaches them. They are also removed during a reorg only for the specific heights being swapped — any fork header at a height that is not directly displaced during the reorg walk remains in `headers_pool` forever.

There is no privileged method (owner-only, DAO-only, or pause-gated) that can remove an arbitrary entry from `headers_pool`. The `Pausable` and `Upgradable` machinery is present, but no `remove_fork_header` or equivalent function exists anywhere in the contract.

---

### Impact Explanation

A fraudulent fork header that reaches `headers_pool` is **irremovable**. Even after the contract is paused (e.g., by `PauseManager` upon fraud detection), the fraudulent entry remains. If the attacker later extends that fork to accumulate more chainwork than the legitimate mainchain, `submit_block_header_inner` will call `reorg_chain` and promote the fraudulent fork to the canonical chain: [5](#0-4) 

Once promoted, `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` will return `true` for transactions in the fraudulent chain, corrupting every downstream SPV proof consumer.

---

### Likelihood Explanation

`submit_blocks` is gated by `#[trusted_relayer]`, which implements a staking/application mechanism managed by `RelayerManager`: [6](#0-5) 

A malicious actor who stakes and becomes an active relayer (or who holds `UnrestrictedSubmitBlocks`) can submit fork headers at will. The PoW requirement raises the cost but does not eliminate the threat — selfish-mining or withholding attacks already assume an adversary capable of producing valid PoW. Once the fraudulent fork header is in `headers_pool`, no operational response (pausing, role revocation) can remove it.

---

### Recommendation

Add a privileged, pause-gated method that allows the DAO or owner to remove specific entries from `headers_pool` by hash. It should:

1. Accept a list of `H256` block hashes to evict.
2. Be callable **only when the contract is paused** (or restricted to `Role::DAO`).
3. Also remove the corresponding entries from `mainchain_height_to_header` and `mainchain_header_to_height` if present, and update `mainchain_tip_blockhash` / `mainchain_initial_blockhash` if the evicted header is either of those sentinels.

This mirrors the recommendation in the external report: a privileged removal method callable in the paused state.

---

### Proof of Concept

1. Trusted relayer calls `submit_blocks` with a valid fork header `F` (prev_block_hash points to a known mainchain block, valid PoW, lower chainwork than current tip).
2. `submit_block_header_inner` takes the `else` branch and calls `store_fork_header(&current_header)` — `F` is now in `headers_pool` only. [7](#0-6) 
3. Fraud is detected; `PauseManager` pauses the contract.
4. `run_mainchain_gc` is called (it has `UnrestrictedRunGC` bypass). It iterates `mainchain_height_to_header` — `F` is not there — so `F` is never removed. [8](#0-7) 
5. No other method can remove `F`. The contract is unpaused; the attacker extends the fork past the mainchain tip; `reorg_chain` promotes the fraudulent chain; SPV proofs for fraudulent transactions now return `true`.

### Citations

**File:** contract/src/lib.rs (L107-108)
```rust
    // Mapping of block hashes to block headers (ALL ever submitted, i.e., incl. forks)
    headers_pool: LookupMap<H256, ExtendedHeader>,
```

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** contract/src/lib.rs (L391-415)
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
        }
```

**File:** contract/src/lib.rs (L549-560)
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
```

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L659-662)
```rust
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

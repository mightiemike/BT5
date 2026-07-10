### Title
GC-Pruned Branching-Point Causes Permanent Reorg Panic, Freezing Canonical Chain at Lower Chainwork — (`File: contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` removes mainchain blocks from `headers_pool` without checking whether any fork block still references them as a parent. If a fork's branching-point block is later GC-pruned, any subsequent `submit_blocks` call that would promote that fork via `reorg_chain` panics unconditionally. The contract's canonical chain is permanently frozen at a lower-chainwork state, and all SPV proofs are evaluated against the wrong chain.

---

### Finding Description

`run_mainchain_gc` removes old mainchain blocks by calling `remove_block_header`, which deletes the entry from both `mainchain_header_to_height` and `headers_pool`: [1](#0-0) [2](#0-1) 

Fork blocks, however, are stored only in `headers_pool` via `store_fork_header` and are **never touched by GC**: [3](#0-2) 

This creates the following desynchronized state:

1. Fork block **F1** is submitted whose parent is mainchain block **M\_H** (M\_H is in `headers_pool` at submission time) — succeeds.
2. The chain advances; GC runs and removes **M\_H** from `headers_pool` (and from `mainchain_header_to_height`).
3. Additional fork blocks **F2, F3, …** are submitted on top of F1 — each succeeds because their immediate parent is a fork block still present in `headers_pool`.
4. The fork's cumulative `chain_work` eventually exceeds the mainchain tip's `chain_work`, triggering the branch in `submit_block_header_inner`: [4](#0-3) 

5. `reorg_chain` walks backward through the fork chain looking for the first block that is still registered in `mainchain_header_to_height`: [5](#0-4) 

6. Because **M\_H** was removed from `mainchain_header_to_height` by GC, the loop does not terminate at M\_H. It steps into F1, reads `prev_block_hash = M_H`, then attempts: [6](#0-5) 

   `headers_pool.get(&M_H)` returns `None` because GC deleted it. The `unwrap_or_else` fires `env::panic_str("previous fork block should be there")`. The NEAR transaction is reverted atomically — **no state change occurs** — so the fork remains in `headers_pool` with higher chainwork, and the mainchain tip remains at the lower-chainwork block. Every future attempt to promote this fork repeats the same panic.

---

### Impact Explanation

The contract's `mainchain_tip_blockhash` is permanently stuck at a block whose `chain_work` is lower than the fork's. Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` resolve the canonical chain through `mainchain_header_to_height`, which now reflects the weaker chain: [7](#0-6) 

Any downstream consumer (bridge, cross-chain protocol, dApp) calling these functions receives SPV proofs evaluated against the wrong chain. Transactions on the heavier fork are rejected; transactions on the lighter (stale) mainchain are accepted. This breaks the core security invariant of the light client.

---

### Likelihood Explanation

The scenario requires three sequential conditions:

1. A fork block is submitted while its mainchain parent is still within the GC window.
2. The mainchain advances by more than `gc_threshold` blocks, causing GC to prune that parent.
3. The fork accumulates more chainwork than the mainchain.

For production Bitcoin mainnet with `skip_pow_verification = false`, condition 3 requires real hashrate, making deliberate exploitation expensive. However, it can occur naturally during a genuine Bitcoin reorganization that spans the GC boundary. For any deployment with `skip_pow_verification = true` (testnet, staging, development), all three conditions are trivially satisfiable by any trusted relayer submitting crafted headers. The `gc_threshold` default of 52,704 blocks (~1 year) means long-lived forks submitted near the GC boundary are the realistic natural trigger. [8](#0-7) 

---

### Recommendation

Before removing a mainchain block in `run_mainchain_gc`, check whether any fork block in `headers_pool` references it as `prev_block_hash`. If so, either refuse to GC that block, or eagerly prune the orphaned fork subtree rooted at that block. Alternatively, change `reorg_chain` to terminate the backward walk at `mainchain_initial_blockhash` (the oldest retained mainchain block) rather than requiring the branching point to still be present in `mainchain_header_to_height`, and reject the reorg with a recoverable error rather than a panic when the ancestor is missing.

---

### Proof of Concept

```
State: gc_threshold = 3, mainchain = [H0, H1, H2, H3, H4]
                                       ^initial          ^tip

Step 1: Submit fork block F1 (prev = H1). F1 stored in headers_pool.
        headers_pool = {H0, H1, H2, H3, H4, F1}

Step 2: Submit mainchain blocks H5, H6, H7 (batch of 3).
        GC fires: removes H0, H1, H2 from headers_pool and mainchain_header_to_height.
        mainchain_initial_blockhash = H3
        headers_pool = {H3, H4, H5, H6, H7, F1}   ← F1 survives, H1 is gone

Step 3: Submit fork blocks F2 (prev=F1), F3 (prev=F2), F4 (prev=F3).
        Each succeeds (parent is in headers_pool).
        F4.chain_work > H7.chain_work  →  reorg triggered.

Step 4: reorg_chain walks: F4 → F3 → F2 → F1
        Loop checks: is F1 in mainchain_header_to_height? No (it's a fork block).
        Reads prev_block_hash of F1 = H1.
        headers_pool.get(H1) → None  →  PANIC "previous fork block should be there"

Result: submit_blocks reverts. mainchain tip stays at H7 (lower chainwork).
        Every future attempt to submit F5, F6, … repeats the panic.
        verify_transaction_inclusion evaluates proofs against H7's chain forever.
```

### Citations

**File:** contract/src/lib.rs (L130-131)
```rust
    /// * `skip_pow_verification = false`: Should be set to `false` for standard use. Set to `true` only for testing purposes.
    /// * `gc_threshold = 52704`: This is the approximate number of blocks generated in a year.
```

**File:** contract/src/lib.rs (L299-301)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
```

**File:** contract/src/lib.rs (L401-408)
```rust
            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
```

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L616-618)
```rust
        while !self
            .mainchain_header_to_height
            .contains_key(&fork_header_cursor.block_hash)
```

**File:** contract/src/lib.rs (L639-642)
```rust
            fork_header_cursor = self
                .headers_pool
                .get(&prev_block_hash)
                .unwrap_or_else(|| env::panic_str("previous fork block should be there"));
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

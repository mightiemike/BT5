### Title
Orphaned Fork Headers Accumulate in `headers_pool` With No GC Path, Permanently Locking NEAR Storage Deposits - (`contract/src/lib.rs`)

---

### Summary

`store_fork_header` writes fork block headers into `headers_pool` but there is no corresponding removal path for losing fork headers. `run_mainchain_gc` exclusively iterates `mainchain_height_to_header`, which fork headers are never inserted into, so they are structurally invisible to GC. The NEAR storage deposit paid by the relayer to cover each fork header's on-chain storage is permanently locked in the contract with no mechanism to reclaim it.

---

### Finding Description

When `submit_block_header_inner` determines that an incoming header does not extend the current mainchain tip, it routes the header to `store_fork_header`:

```rust
// contract/src/lib.rs  lines 549-566
} else {
    log!("Block {}: saving to fork", current_header.block_hash);
    ...
    self.store_fork_header(&current_header);

    if current_header.chain_work > total_main_chain_chainwork {
        log!("Chain reorg");
        self.reorg_chain(current_header, last_main_chain_block_height);
    }
}
```

`store_fork_header` inserts only into `headers_pool`:

```rust
// contract/src/lib.rs  lines 664-667
fn store_fork_header(&mut self, header: &ExtendedHeader) {
    self.headers_pool.insert(&header.block_hash, header);
}
```

It does **not** insert into `mainchain_height_to_header` or `mainchain_header_to_height`. The GC function `run_mainchain_gc` discovers blocks to remove exclusively by iterating `mainchain_height_to_header`:

```rust
// contract/src/lib.rs  lines 401-408
for height in start_removal_height..end_removal_height {
    let blockhash = &self
        .mainchain_height_to_header
        .get(&height)
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
    self.remove_block_header(blockhash);
    self.mainchain_height_to_header.remove(&height);
}
```

Because fork headers are absent from `mainchain_height_to_header`, they are structurally invisible to GC and are never passed to `remove_block_header`. The only other caller of `remove_block_header` is `reorg_chain`, which removes **old mainchain blocks** that are displaced during a reorg — it never removes losing fork blocks that were submitted for a fork that did not win.

The result is a one-way write: every fork header submitted accumulates permanently in `headers_pool` with no deletion path.

`submit_blocks` is `#[payable]` and charges the caller a storage deposit proportional to `diff_storage_usage`:

```rust
// contract/src/lib.rs  lines 182-188
let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());
require!(
    amount >= required_deposit,
    format!("Required deposit {}", required_deposit)
);
```

Because fork headers are never removed, the NEAR tokens deposited to cover their storage are never freed. The contract's storage obligation grows monotonically with every fork submission, and the deposited NEAR is permanently locked.

---

### Impact Explanation

Every fork header submitted by a relayer costs a NEAR storage deposit that is never returned. Over the operational lifetime of the contract — which tracks a live PoW chain that naturally produces orphan blocks and short forks — `headers_pool` grows without bound. The NEAR tokens locked as storage deposits accumulate permanently in the contract account with no administrative or user-facing function to reclaim them. This is the direct analog to the original report: a one-way deposit operation (fork header submission) with no corresponding withdrawal/GC path, leading to permanently locked funds.

---

### Likelihood Explanation

Bitcoin and all supported chains (Litecoin, Dogecoin, Zcash) produce orphan blocks and short forks continuously under normal operation. The relayer is designed to submit these headers as part of its fork-tracking logic. No adversarial behavior is required; the locked-deposit condition is triggered by routine chain operation. Any trusted relayer following the normal submission path will continuously accumulate locked storage deposits.

---

### Recommendation

Extend `run_mainchain_gc` (or introduce a separate GC function) to also evict fork headers from `headers_pool` once they are sufficiently old and their fork has been definitively abandoned (e.g., their `block_height` is below `mainchain_initial_blockhash.block_height`). Since fork headers are not indexed by height in any map, a separate age-tracking structure (e.g., a height-keyed set of fork hashes) would be needed to make them discoverable for GC. Alternatively, refund the storage deposit for fork headers at the point they are determined to be losing (i.e., when the mainchain advances far enough past their height).

---

### Proof of Concept

1. Initialize the contract with a genesis block.
2. Submit a batch of headers that form a valid fork branch (prev_block_hash points to a mainchain ancestor, not the current tip). These are routed through `submit_block_header_inner` → `store_fork_header` and land in `headers_pool`.
3. Continue submitting mainchain headers so the fork never wins. Call `run_mainchain_gc` with any batch size.
4. Observe that the fork headers remain in `headers_pool` indefinitely — `mainchain_height_to_header.get(&fork_height)` returns `None` for those heights, so GC never touches them.
5. The NEAR storage deposit paid in step 2 is never refunded; `env::storage_usage()` reflects the permanently retained fork entries.

The root cause is confirmed at:
- [1](#0-0)  — `store_fork_header` writes to `headers_pool` only, with no height-index entry.
- [2](#0-1)  — GC iterates only `mainchain_height_to_header`, making fork headers structurally invisible.
- [3](#0-2)  — `remove_block_header` is never called for losing fork headers.
- [4](#0-3)  — Storage deposit is charged per call and never returned for fork-header storage.

### Citations

**File:** contract/src/lib.rs (L182-197)
```rust
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

**File:** contract/src/lib.rs (L401-414)
```rust
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

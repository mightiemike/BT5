### Title
GC-Induced Gap in `headers_pool` Causes Permanent `submit_blocks` Revert at Difficulty Adjustment Boundaries — (File: `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`, `contract/src/lib.rs`)

---

### Summary

The contract's garbage-collection (GC) mechanism removes old block headers from `headers_pool` and `mainchain_height_to_header`. The difficulty-adjustment logic in Bitcoin and Litecoin builds requires a single, non-fallback lookup of the block at the start of the current difficulty interval via `get_header_by_height`. When GC has removed that block, `get_header_by_height` panics unconditionally. There is no alternative data source and no recovery path. Every subsequent `submit_blocks` call at a difficulty-adjustment boundary permanently reverts.

---

### Finding Description

**Root cause — `get_header_by_height` is the sole data source for difficulty adjustment:**

In `bitcoin.rs`, `get_next_work_required` computes the first block of the current 2016-block interval and fetches it:

```rust
let first_block_height =
    prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
``` [1](#0-0) 

The identical pattern exists in `litecoin.rs`:

```rust
let first_block_height = prev_block_header.block_height - blocks_to_go_back;
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
``` [2](#0-1) 

`get_header_by_height` has no fallback — it panics unconditionally if the height is absent:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [3](#0-2) 

**Root cause — GC removes exactly those blocks:**

`run_mainchain_gc` (called from every `submit_blocks`) removes old blocks from both `mainchain_height_to_header` and `headers_pool`:

```rust
for height in start_removal_height..end_removal_height {
    let blockhash = &self.mainchain_height_to_header
        .get(&height)
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
    self.remove_block_header(blockhash);
    self.mainchain_height_to_header.remove(&height);
}
``` [4](#0-3) 

`remove_block_header` deletes the entry from `headers_pool`: [5](#0-4) 

**Root cause — no minimum `gc_threshold` enforcement:**

`init` accepts `gc_threshold` with no lower-bound validation against `difficulty_adjustment_interval`: [6](#0-5) 

When `gc_threshold < difficulty_adjustment_interval` (2016 for Bitcoin/Litecoin), GC will eventually remove the block at `first_block_height`. From that point on, every `submit_blocks` call at a retarget boundary panics with `ERR_KEY_NOT_EXIST`.

---

### Impact Explanation

Once the required historical block is GC'd, `submit_blocks` permanently reverts for every block at a difficulty-adjustment boundary. The light client can no longer advance its canonical chain tip. All downstream consumers of `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are permanently broken because the chain tip stops updating. There is no recovery path — no fallback data source, no way to re-insert the GC'd block, and no way to skip the difficulty check.

---

### Likelihood Explanation

The `gc_threshold` parameter is set once at `init` with no documented minimum and no on-chain enforcement. Any deployer who sets `gc_threshold` below 2016 (e.g., for a resource-constrained or test-like deployment) will trigger this condition at the first retarget boundary (~2016 blocks in). The README recommends 52704 but does not explain the invariant, making it easy to violate. The trigger is deterministic and reproducible: every retarget boundary after GC crosses the interval start height will revert.

---

### Recommendation

1. Add a `require` in `init` enforcing `gc_threshold >= difficulty_adjustment_interval` (and for Zcash, `>= pow_averaging_window + MEDIAN_TIME_SPAN`).
2. In `get_header_by_height`, return an `Option<ExtendedHeader>` and propagate the absence gracefully rather than panicking, so callers can emit a descriptive error instead of a hard revert.
3. Document the invariant explicitly so operators understand the minimum safe `gc_threshold`.

---

### Proof of Concept

1. Deploy the Bitcoin build with `gc_threshold = 100`.
2. Submit 2100 sequential valid block headers via `submit_blocks`. GC runs after each batch, eventually removing all blocks below height ~2000.
3. Submit the block at height 2017 (first block after the retarget at height 2016). `check_pow` → `get_next_work_required` → `get_header_by_height(1)` → `ERR_KEY_NOT_EXIST` → transaction reverts.
4. Every subsequent `submit_blocks` call at any retarget boundary (4032, 6048, …) also reverts. The light client is permanently stalled.

### Citations

**File:** contract/src/bitcoin.rs (L78-86)
```rust
    let first_block_height =
        prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```

**File:** contract/src/litecoin.rs (L86-93)
```rust
    let first_block_height = prev_block_header.block_height - blocks_to_go_back;

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```

**File:** contract/src/lib.rs (L135-161)
```rust
    pub fn init(args: InitArgs) -> Self {
        let mut contract = Self {
            mainchain_height_to_header: LookupMap::new(StorageKey::MainchainHeightToHeader),
            mainchain_header_to_height: LookupMap::new(StorageKey::MainchainHeaderToHeight),
            headers_pool: LookupMap::new(StorageKey::HeadersPool),
            mainchain_initial_blockhash: H256::default(),
            mainchain_tip_blockhash: H256::default(),
            skip_pow_verification: args.skip_pow_verification,
            gc_threshold: args.gc_threshold,
            network: args.network,
        };

        // Make the contract itself super admin. This allows us to grant any role in the
        // constructor.
        near_sdk::require!(
            contract.acl_init_super_admin(env::current_account_id()),
            "Failed to initialize super admin",
        );

        contract.init_genesis(
            &args.genesis_block_hash,
            args.genesis_block_height,
            args.submit_blocks,
        );

        contract
    }
```

**File:** contract/src/lib.rs (L401-409)
```rust
            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
            }
```

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```

**File:** contract/src/lib.rs (L677-682)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```

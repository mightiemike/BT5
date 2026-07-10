### Title
GC Threshold Below `difficulty_adjustment_interval` Permanently Breaks Difficulty Retarget — (`contract/src/bitcoin.rs`, `contract/src/lib.rs`)

---

### Summary

`init()` accepts any `gc_threshold` value without enforcing a minimum of `difficulty_adjustment_interval` (2016 for Bitcoin). `run_mainchain_gc()` blindly prunes the oldest mainchain blocks with no awareness of which heights are required by `get_next_work_required()`. Once GC removes the block at the start of a difficulty period, every subsequent submission at a difficulty boundary panics with `ERR_KEY_NOT_EXIST`, permanently locking out all further block submissions.

---

### Finding Description

**Entrypoint — `init()` accepts unchecked `gc_threshold`**

`InitArgs.gc_threshold` is stored verbatim with no lower-bound validation: [1](#0-0) 

The documentation only *recommends* `gc_threshold = 52704` but enforces nothing: [2](#0-1) 

**GC removes blocks without preserving difficulty-period anchors**

`run_mainchain_gc()` removes the oldest `selected_amount_to_remove` blocks from `mainchain_height_to_header` and `headers_pool` unconditionally: [3](#0-2) 

There is no check that the block at `period_start_height = (current_tip / 2016) * 2016` is retained.

**Difficulty retarget requires the period-start block by height**

At every 2016-block boundary, `get_next_work_required()` computes:

```
first_block_height = prev_block_header.block_height - (difficulty_adjustment_interval - 1)
```

and immediately calls `get_header_by_height(first_block_height)`: [4](#0-3) 

**`get_header_by_height` panics if the key is absent** [5](#0-4) 

---

### Impact Explanation

Once `gc_threshold < difficulty_adjustment_interval`, GC will eventually remove the block at the start of the current difficulty period. The very next `submit_blocks()` call that crosses a difficulty boundary panics with `ERR_KEY_NOT_EXIST`. Because `gc_threshold` is immutable after `init()` and there is no mechanism to re-insert pruned headers, the contract is permanently unable to accept any block at a difficulty boundary. All SPV proof verification for blocks beyond that point is also permanently blocked.

---

### Likelihood Explanation

The `#[private] #[init]` restriction means only the contract deployer calls `init()`. However, the vulnerability does not require a malicious actor — it is triggered by any operator who sets `gc_threshold` below 2016 (e.g., for storage cost reduction or testing). The existing test suite itself uses `gc_threshold = 10` and `gc_threshold = 20`: [6](#0-5) 

The documentation's recommendation is advisory only, and no on-chain guard prevents deployment with a dangerously low threshold.

---

### Recommendation

Add a `require!` in `init_genesis()` (or `init()`) enforcing:

```rust
require!(
    args.gc_threshold >= config.difficulty_adjustment_interval,
    format!(
        "gc_threshold ({}) must be >= difficulty_adjustment_interval ({})",
        args.gc_threshold, config.difficulty_adjustment_interval
    )
);
```

Additionally, `run_mainchain_gc()` should never remove the block at `(tip_height / difficulty_adjustment_interval) * difficulty_adjustment_interval`, preserving the period-start anchor regardless of `gc_threshold`.

---

### Proof of Concept

```
1. Deploy Bitcoin build with InitArgs { gc_threshold: 100, genesis_block_height: 0, ... }
2. submit_blocks([block_1 .. block_2016])          // 2016 headers in one call
   - block_2016 triggers retarget: get_header_by_height(0) → OK (not yet GC'd)
   - GC runs with batch_size=2016: removes heights 0..1915; initial → height 1916
3. submit_blocks([block_2017 .. block_2116])        // 100 more headers
   - GC runs with batch_size=100: removes heights 1916..2015; initial → height 2016
4. submit_blocks([block_2117 .. block_2216])        // 100 more headers
   - GC runs: removes heights 2016..2115; initial → height 2116
   - Height 2016 is now GONE from mainchain_height_to_header
5. Continue submitting until prev_height = 4031 (second difficulty boundary)
   submit_blocks([block_4032])
   → get_next_work_required: first_block_height = 4031 - 2015 = 2016
   → get_header_by_height(2016) → env::panic_str("ERR_KEY_NOT_EXIST")  ✓
```

The contract is now permanently unable to accept any block at a difficulty boundary.

### Citations

**File:** contract/src/lib.rs (L127-131)
```rust
    /// Recommended initialization parameters:
    /// * `genesis_block_height % difficulty_adjustment_interval == 0`: The genesis block height must be divisible by `difficulty_adjustment_interval` to align with difficulty adjustment cycles.
    /// * The `genesis_block` must be at least 144 blocks earlier than the last block. 144 is the approximate number of blocks generated in one day.
    /// * `skip_pow_verification = false`: Should be set to `false` for standard use. Set to `true` only for testing purposes.
    /// * `gc_threshold = 52704`: This is the approximate number of blocks generated in a year.
```

**File:** contract/src/lib.rs (L135-143)
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
```

**File:** contract/src/lib.rs (L391-414)
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

**File:** contract/tests/test_dogecoin.rs (L103-110)
```rust
        let args = InitArgs {
            genesis_block_hash: genesis.block_hash(),
            genesis_block_height: 0,
            skip_pow_verification: false,
            gc_threshold: 20,
            network: Network::Mainnet,
            submit_blocks: init_blocks,
        };
```

### Title
`gc_threshold` Not Validated Against `difficulty_adjustment_interval`, Causing Permanent Block Submission DoS — (File: `contract/src/lib.rs`)

---

### Summary

The `init` function accepts any `gc_threshold` value without enforcing that it is at least as large as the chain's `difficulty_adjustment_interval`. If `gc_threshold < difficulty_adjustment_interval`, the GC will prune the block needed by `get_next_work_required` before the first difficulty-adjustment boundary is reached, causing every subsequent `submit_blocks` call at that boundary to panic with `ERR_KEY_NOT_EXIST`. There is no recovery path short of a contract upgrade.

---

### Finding Description

`BtcLightClient::init` stores the caller-supplied `gc_threshold` directly into contract state with no lower-bound check against the chain's `difficulty_adjustment_interval`: [1](#0-0) 

`run_mainchain_gc` is called on every `submit_blocks` invocation and removes the oldest mainchain headers whenever the stored count exceeds `gc_threshold`: [2](#0-1) 

When a difficulty-adjustment block is submitted for Bitcoin or Litecoin, `get_next_work_required` computes `first_block_height = prev_block_header.block_height − (difficulty_adjustment_interval − 1)` and calls `get_header_by_height` on that height: [3](#0-2) 

`get_header_by_height` panics with `ERR_KEY_NOT_EXIST` if the block is absent from the pool: [4](#0-3) 

For Bitcoin, `difficulty_adjustment_interval = 2016`: [5](#0-4) 

If `gc_threshold = 100`, after 101 blocks the GC begins pruning. By the time the chain reaches height 2015 (the block before the first adjustment boundary), the genesis block at height 0 has long been removed. The call `get_header_by_height(0)` panics, and no further blocks can ever be submitted.

The same desynchronization applies to Litecoin (same `difficulty_adjustment_interval = 2016`) and to Zcash, where `zcash_get_next_work_required` traverses back `pow_averaging_window + MEDIAN_TIME_SPAN = 17 + 11 = 28` blocks on every single block submission: [6](#0-5) 

For Zcash, any `gc_threshold < 28` triggers the panic after only 28 submitted blocks.

Notably, `verify_transaction_inclusion` already contains an analogous guard (`args.confirmations <= self.gc_threshold`), but no equivalent guard exists for the PoW/difficulty path: [7](#0-6) 

---

### Impact Explanation

Once the panic is triggered, every call to `submit_blocks` at a difficulty-adjustment boundary reverts permanently. The contract's canonical chain freezes; `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` can only confirm transactions in already-stored blocks. Any downstream NEAR contract consuming SPV proofs for new Bitcoin/Litecoin/Zcash transactions is permanently broken. There is no on-chain fallback (no equivalent of `finalize()`); recovery requires a privileged contract upgrade.

---

### Likelihood Explanation

The inline documentation recommends `gc_threshold = 52704` (≈ one year of Bitcoin blocks), which is safely above 2016. However, nothing in the contract enforces this. A deployer who sets a smaller value — e.g., to reduce storage costs or by simple misconfiguration — silently arms the trap. The failure is invisible until the first difficulty-adjustment boundary is crossed, which for Bitcoin is 2016 blocks (~2 weeks) after genesis. For Zcash the failure window is only 28 blocks. The relayer, an unprivileged NEAR caller, is the unwitting trigger.

---

### Recommendation

Add an explicit lower-bound check inside `init` (or `init_genesis`) that asserts `gc_threshold >= difficulty_adjustment_interval` for Bitcoin/Litecoin, and `gc_threshold >= pow_averaging

### Citations

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

**File:** contract/src/lib.rs (L289-292)
```rust
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );
```

**File:** contract/src/lib.rs (L388-415)
```rust
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

**File:** contract/src/bitcoin.rs (L78-87)
```rust
    let first_block_height =
        prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
}
```

**File:** btc-types/src/network.rs (L29-29)
```rust
            difficulty_adjustment_interval: 2016,
```

**File:** contract/src/zcash.rs (L92-103)
```rust
        for i in 0..usize::try_from(config.pow_averaging_window).unwrap() {
            if i < MEDIAN_TIME_SPAN {
                median_time[i] = current_header.block_header.time;
            }

            let (sum, overflow) =
                total_target.overflowing_add(target_from_bits(current_header.block_header.bits));
            require!(!overflow, "Addition of U256 values overflowed");
            total_target = sum;

            current_header = prev_block_getter.get_prev_header(&current_header.block_header);
        }
```

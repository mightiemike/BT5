### Title
Fork Difficulty Calculation Uses Stale Mainchain Boundary Block Instead of Fork Ancestor, Enabling Difficulty Manipulation — (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`)

---

### Summary

When validating fork blocks at difficulty-adjustment boundaries, all three non-Zcash chain implementations call `get_header_by_height` to retrieve the first block of the retarget period. That function exclusively reads from `mainchain_height_to_header`, which only contains mainchain blocks. Fork blocks are stored only in `headers_pool`. The result is a cross-module desynchronization: the difficulty calculation uses the mainchain's boundary-block timestamp instead of the fork's actual ancestor's timestamp, allowing an attacker to submit fork blocks whose `bits` field does not match what the real network would require.

---

### Finding Description

`get_header_by_height` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header          // only mainchain
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [1](#0-0) 

Fork blocks are stored via `store_fork_header`, which inserts only into `headers_pool`:

```rust
fn store_fork_header(&mut self, header: &ExtendedHeader) {
    self.headers_pool.insert(&header.block_hash, header);
}
``` [2](#0-1) 

They are **never** inserted into `mainchain_height_to_header`. Therefore, when `get_next_work_required` calls `get_header_by_height` to obtain the boundary block's timestamp, it silently returns the **mainchain** block at that height, not the fork's actual ancestor.

**Bitcoin** (`bitcoin.rs` line 81):
```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
``` [3](#0-2) 

**Litecoin** (`litecoin.rs` line 88):
```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
``` [4](#0-3) 

**Dogecoin** (`dogecoin.rs` lines 292–295) — the developers themselves flagged this with a TODO:
```rust
// TODO: check if it is correct to get block header by height from mainchain
// without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [5](#0-4) 

By contrast, the **Zcash** implementation correctly traverses the chain backwards using `get_prev_header`, which resolves blocks by hash from `headers_pool` and therefore works correctly for both mainchain and fork blocks: [6](#0-5) 

The desynchronization is structurally identical to the external report: a state variable (the boundary block's timestamp, drawn from the mainchain map) is used for a calculation (expected `bits`) at a point where the relevant state (the fork's ancestor at that height) has not been reflected into the lookup structure. The calculation therefore uses stale/wrong state.

---

### Impact Explanation

The contract enforces:
```rust
require!(
    expected_bits == block_header.bits,
    "bad-diffbits: incorrect proof of work"
);
``` [7](#0-6) 

Because `expected_bits` is derived from the wrong boundary-block timestamp, the contract will:

1. **Accept** fork blocks whose `bits` field matches the incorrectly computed `expected_bits` but does not match what the real network would require.
2. **Reject** legitimate fork blocks whose `bits` field is correct for the real network but does not match the incorrectly computed `expected_bits`.

For **Dogecoin post-block 145 000**, `difficulty_adjustment_interval` becomes `1` (every block is a retarget): [8](#0-7) 

This means the desynchronization affects **every single fork block** submitted after that height. An attacker can choose fork-block timestamps so that `fork_block_N.time − mainchain_block_{N−1}.time` is maximized (up to the Digishield cap of `retarget_timespan + retarget_timespan/2`), yielding a lower expected difficulty. The attacker then mines fork blocks at that reduced difficulty, submits them with the matching lower `bits`, and the contract accepts them. If the fork accumulates sufficient chain work it triggers a reorg via `reorg_chain`, causing the contract's canonical chain to diverge from the real Dogecoin network — invalidating all SPV proofs anchored to the promoted fork.

For **Bitcoin and Litecoin**, the impact is scoped to the single block at each 2016-block retarget boundary, but the same acceptance/rejection inversion applies.

---

### Likelihood Explanation

- Any unprivileged NEAR caller can invoke `submit_blocks` with adversarially crafted fork headers.
- For Dogecoin post-145 000, the vulnerable code path is exercised on **every** fork block submitted.
- For Bitcoin/Litecoin, the path is exercised once per 2016-block epoch boundary that the fork crosses.
- No privileged role, leaked key, or social engineering is required.
- The attacker-controlled entry point is `submit_blocks` → `submit_block_header` → `check_target` → `check_pow` → `get_next_work_required` → `get_header_by_height`. [9](#0-8) 

---

### Recommendation

Replace `get_header_by_height` calls inside `get_next_work_required` with backward chain traversal using `get_prev_header`, exactly as the Zcash implementation does. Starting from `prev_block_header`, walk backwards `blocks_to_go_back` steps through `prev_block_hash` links. This resolves blocks from `headers_pool` by hash and therefore correctly follows the fork's own ancestry rather than the mainchain's height index.

---

### Proof of Concept

**Dogecoin post-145 000 scenario (every block is a retarget):**

1. Mainchain contains blocks at heights 0 … N (N > 145 000). Mainchain block at height N−1 has timestamp `T_main`.
2. Attacker submits a fork diverging at height K < N−1. Fork block at height N has timestamp `T_fork` set to `T_main + max_allowed_delta` (within MTP and future-time constraints).
3. Contract calls `get_next_work_required` for the fork block at height N+1:
   - `height_first = N − 1`
   - `get_header_by_height(N−1)` → returns **mainchain** block with timestamp `T_main` (fork's block at N−1 is not in `mainchain_height_to_header`)
   - `actual_time = T_fork − T_main` → large value → clamped to `max_timespan = 90 s`
   - `expected_bits` → lower difficulty than the real network requires
4. Attacker mines the fork block at height N+1 with the lower `bits` value (easier PoW).
5. Contract accepts it: `expected_bits == block_header.bits` ✓, PoW check passes ✓.
6. Repeated over many blocks, the fork accumulates chain work and triggers `reorg_chain`, promoting the attacker's fork to the canonical chain. [10](#0-9) [11](#0-10)

### Citations

**File:** contract/src/lib.rs (L169-198)
```rust
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
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

**File:** contract/src/lib.rs (L665-667)
```rust
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
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

**File:** contract/src/bitcoin.rs (L23-26)
```rust
        require!(
            expected_bits == block_header.bits,
            "bad-diffbits: incorrect proof of work"
        );
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

**File:** contract/src/dogecoin.rs (L229-297)
```rust
fn get_next_work_required(
    config: &DogecoinConfig,
    block_header: &Header,
    prev_block_header: &ExtendedHeader,
    blocks_getter: &impl BlocksGetter,
) -> u32 {
    // Dogecoin: Special rules for minimum difficulty blocks with Digishield
    if allow_min_difficulty_for_block(config, block_header, prev_block_header) {
        // Special difficulty rule for testnet:
        // If the new block's timestamp is more than 2* nTargetSpacing minutes
        // then allow mining of a min-difficulty block.
        return config.proof_of_work_limit_bits;
    }

    // Only change once per difficulty adjustment interval
    let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
    let difficulty_adjustment_interval = if new_difficulty_protocol {
        1
    } else {
        config.difficulty_adjustment_interval
    };

    if (prev_block_header.block_height + 1) % difficulty_adjustment_interval != 0 {
        if config.pow_allow_min_difficulty_blocks {
            // Special difficulty rule for testnet:
            // If the new block's timestamp is more than 2* 10 minutes
            // then allow mining of a min-difficulty block.
            if block_header.time
                > prev_block_header.block_header.time + config.pow_target_spacing * 2
            {
                return config.proof_of_work_limit_bits;
            }

            // Return the last non-special-min-difficulty-rules-block
            let mut current_block_header = prev_block_header.clone();

            while current_block_header.block_header.bits == config.proof_of_work_limit_bits
                && current_block_header.block_height % config.difficulty_adjustment_interval != 0
            {
                current_block_header =
                    blocks_getter.get_prev_header(&current_block_header.block_header);
            }

            return current_block_header.block_header.bits;
        }

        return prev_block_header.block_header.bits;
    }

    // Litecoin: This fixes an issue where a 51% attack can change difficulty at will.
    // Go back the full period unless it's the first retarget after genesis. Code courtesy of Art Forz
    let mut blocks_to_go_back = difficulty_adjustment_interval - 1;
    if prev_block_header.block_height + 1 != difficulty_adjustment_interval {
        blocks_to_go_back = difficulty_adjustment_interval;
    }

    // Go back by what we want to be 14 days worth of blocks
    let height_first = prev_block_header
        .block_height
        .checked_sub(blocks_to_go_back)
        .unwrap_or_else(|| env::panic_str("Height underflow when calculating first block height"));

    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;

    calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
```

**File:** contract/src/zcash.rs (L87-103)
```rust
    let mut current_header = prev_block_header.clone();
    let mut total_target = U256::ZERO;
    let mut median_time = [0u32; MEDIAN_TIME_SPAN];

    let prev_block_median_time_past = {
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

### Title
Fork Difficulty Calculated from Stale Mainchain Boundary Block Instead of Fork's Actual Ancestor - (`contract/src/bitcoin.rs`, `contract/src/dogecoin.rs`)

### Summary
When validating a fork block that falls on a difficulty-adjustment boundary, both the Bitcoin and Dogecoin difficulty-calculation paths call `get_header_by_height` to retrieve the interval's first block. That helper always returns the **mainchain** block at the requested height, not the fork's actual ancestor. If the fork diverged before the boundary, the two blocks differ, and the contract computes the wrong expected difficulty for the fork. A malicious relayer can exploit this to submit a fork whose blocks are accepted at an artificially easy difficulty, then accumulate enough chainwork to trigger a reorg and corrupt the canonical chain.

### Finding Description

`get_header_by_height` is implemented as:

```rust
// contract/src/lib.rs  lines 677-682
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
```

It unconditionally resolves the height through `mainchain_height_to_header`, so it always returns the **mainchain** block, never a fork ancestor. [1](#0-0) 

Both difficulty-adjustment paths call this function at the critical boundary lookup:

**Bitcoin** (`get_next_work_required`, line 81):
```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
``` [2](#0-1) 

**Dogecoin** (`get_next_work_required`, lines 291-295 — the TODO comment is the developers' own acknowledgement of the problem):
```rust
// TODO: check if it is correct to get block header by height from mainchain
// without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [3](#0-2) 

When a fork block sits exactly on a difficulty-adjustment boundary and the fork diverged before `first_block_height`, the mainchain block at that height and the fork's true ancestor at that height are **different blocks with different timestamps**. The time-span used in `calculate_next_work_required` is therefore wrong, producing an `expected_bits` value that does not correspond to the fork's actual ancestry. [4](#0-3) [5](#0-4) 

### Impact Explanation

`check_pow` enforces `expected_bits == block_header.bits` and then checks the PoW hash against `target_from_bits(header.bits)`. [6](#0-5) 

If the mainchain boundary block has a longer elapsed time than the fork's true boundary block, `expected_bits` encodes an **easier** target. The attacker crafts a fork whose `bits` field matches this easier target, mines the fork at the lower difficulty, and submits it. Once the fork's cumulative `chain_work` exceeds the mainchain's, `submit_block_header_inner` triggers `reorg_chain`, replacing the canonical chain with the attacker's fork. [7](#0-6) 

The corrupted canonical chain then serves as the ground truth for all `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` calls, meaning SPV proofs for transactions that never existed on the real Bitcoin chain can be made to return `true`. [8](#0-7) 

### Likelihood Explanation

`submit_blocks` is gated by the `#[trusted_relayer]` macro, but the scope explicitly covers "relayer-path user supplying adversarial chain data." A registered relayer (or one that stakes the required amount) can submit any sequence of syntactically valid headers. Constructing a fork that diverges before a difficulty-adjustment boundary and whose mainchain boundary block has a favorable timestamp is straightforward: the attacker only needs to observe the existing mainchain state and choose a divergence point accordingly. No private key leakage or social engineering is required beyond holding a relayer stake. [9](#0-8) 

### Recommendation

Replace `get_header_by_height` calls inside fork-path difficulty calculations with an ancestor walk that follows `prev_block_hash` links through `headers_pool` until the target height is reached. This mirrors what Bitcoin Core does: it walks the chain of `pprev` pointers rather than looking up a block by height in the active-chain index. For Dogecoin post-145 000 (per-block retarget), the same fix applies to every block in the fork.

### Proof of Concept

1. Mainchain has blocks at heights 0–2016 with a short inter-block time (e.g., 5 min each), producing a high-difficulty `expected_bits` for height 2017.
2. Attacker prepares a fork diverging at height 1 with blocks whose timestamps at height 2015 are spread far apart (e.g., 8 weeks), which would normally produce an easy `expected_bits` for height 2017 on the fork.
3. Attacker submits fork blocks 2–2016 via `submit_blocks`. Each non-boundary block passes `check_pow` using `prev_block_header.block_header.bits` (the fork's own previous bits), so no anomaly is detected yet.
4. Attacker submits fork block 2017. `get_next_work_required` calls `get_header_by_height(1)`, which returns the **mainchain** block at height 1 (short time span → hard difficulty), not the fork's block at height 1 (long time span → easy difficulty). The contract therefore enforces the mainchain's hard difficulty on the fork's boundary block — in this direction the attacker is *disadvantaged*.
5. Conversely, if the mainchain boundary block has a **longer** time span than the fork's true ancestor (e.g., mainchain had a slow period while the fork's ancestor was fast), `expected_bits` is easier than the fork's true ancestry warrants. The attacker mines the fork at this easier difficulty, accumulates chainwork faster than expected, and triggers `reorg_chain`. [10](#0-9) [11](#0-10)

### Citations

**File:** contract/src/lib.rs (L166-198)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
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

**File:** contract/src/lib.rs (L288-323)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

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

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L562-567)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
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

**File:** contract/src/lib.rs (L677-682)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```

**File:** contract/src/bitcoin.rs (L19-46)
```rust
    pub(crate) fn check_pow(&self, block_header: &Header, prev_block_header: &ExtendedHeader) {
        let config = self.get_config();
        let expected_bits = get_next_work_required(&config, block_header, prev_block_header, self);

        require!(
            expected_bits == block_header.bits,
            "bad-diffbits: incorrect proof of work"
        );

        // Check timestamp against prev
        require!(
            block_header.time > get_median_time_past(prev_block_header.clone(), self),
            "time-too-old: block's timestamp is too early"
        );

        // Check timestamp
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
        );

        // Reject blocks with outdated version
        require!(
            block_header.version >= 4,
            "bad-version: block version must be at least 4"
        );
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

**File:** contract/src/bitcoin.rs (L90-118)
```rust
fn calculate_next_work_required(
    config: &NetworkConfig,
    prev_block_header: &ExtendedHeader,
    first_block_time: i64,
) -> u32 {
    let prev_block_time: i64 = prev_block_header.block_header.time.into();

    let mut actual_time_taken: i64 = prev_block_time - first_block_time;
    if actual_time_taken < config.pow_target_timespan / 4 {
        actual_time_taken = config.pow_target_timespan / 4;
    }
    if actual_time_taken > config.pow_target_timespan * 4 {
        actual_time_taken = config.pow_target_timespan * 4;
    }

    let new_target = target_from_bits(prev_block_header.block_header.bits);

    let (mut new_target, new_target_overflow) =
        new_target.overflowing_mul(<i64 as TryInto<u64>>::try_into(actual_time_taken).unwrap());
    require!(!new_target_overflow, "new target overflow");
    new_target = new_target
        / U256::from(<i64 as TryInto<u64>>::try_into(config.pow_target_timespan).unwrap());

    if new_target > config.pow_limit {
        new_target = config.pow_limit;
    }

    new_target.target_to_bits()
}
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

**File:** contract/src/dogecoin.rs (L300-333)
```rust
// source https://github.com/dogecoin/dogecoin/blob/2c513d0172e8bc86fe9a337693b26f2fdf68a013/src/dogecoin.cpp#L41
fn calculate_next_work_required(
    config: &DogecoinConfig,
    prev_block_header: &ExtendedHeader,
    first_block_time: i64,
) -> u32 {
    let retarget_timespan = config.pow_target_timespan;
    let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;

    let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;

    let min_timespan = retarget_timespan - (retarget_timespan / 4);
    let max_timespan = retarget_timespan + (retarget_timespan / 2);

    if modulated_timespan < min_timespan {
        modulated_timespan = min_timespan;
    } else if modulated_timespan > max_timespan {
        modulated_timespan = max_timespan;
    }

    let new_target = target_from_bits(prev_block_header.block_header.bits);

    let (mut new_target, new_target_overflow) =
        new_target.overflowing_mul(<i64 as TryInto<u64>>::try_into(modulated_timespan).unwrap());
    require!(!new_target_overflow, "new target overflow");
    new_target =
        new_target / U256::from(<i64 as TryInto<u64>>::try_into(retarget_timespan).unwrap());

    if new_target > config.pow_limit {
        new_target = config.pow_limit;
    }

    new_target.target_to_bits()
}
```

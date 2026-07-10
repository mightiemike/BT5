### Title
Wrong Ancestor Lookup for Difficulty Calculation on Fork Submissions — (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`)

### Summary

All three chain-specific difficulty-retarget functions call `blocks_getter.get_header_by_height(height_first)` to obtain the boundary block for the difficulty window. `get_header_by_height` is implemented to look up only the **mainchain** height-to-hash map. When the block being validated is a fork block, the correct boundary ancestor must be found by walking backwards along the fork chain, not by reading the mainchain map. Using the mainchain block at that height instead of the fork's actual ancestor produces a wrong `first_block_time`, which corrupts the expected-bits calculation and allows fork blocks with an incorrect difficulty target to pass `check_pow`.

The Dogecoin implementation even carries an explicit developer acknowledgement of this uncertainty:

```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
```

### Finding Description

`get_header_by_height` is implemented in `lib.rs` as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [1](#0-0) 

It reads exclusively from `mainchain_height_to_header`. Fork blocks are stored only in `headers_pool` (via `store_fork_header`), never in `mainchain_height_to_header`. [2](#0-1) 

When a fork block at height H is submitted, `prev_block_header` is the fork's parent. The difficulty boundary height is computed as `height_first = prev_block_header.block_height - blocks_to_go_back`. For Dogecoin (post-145,000), `blocks_to_go_back = 1`, so `height_first = H - 2`. If the fork diverged at height D ≤ H-2, the fork's actual ancestor at H-2 is a fork block, but `get_header_by_height(H-2)` returns the **mainchain** block at H-2. [3](#0-2) 

The same pattern appears in Bitcoin and Litecoin: [4](#0-3) [5](#0-4) 

The wrong `first_block_time` is then fed into `calculate_next_work_required`, producing an incorrect `expected_bits`. The check `require!(expected_bits == block_header.bits, ...)` therefore validates against the wrong target. [6](#0-5) 

### Impact Explanation

**Impact: High**

The difficulty target (`bits`) directly controls two security-critical values:

1. **PoW acceptance threshold** — `require!(U256::from_le_bytes(&pow_hash.0) <= target_from_bits(header.bits))`. A higher `bits` (easier target) lets the attacker mine a valid block with less work.
2. **Chainwork accumulation** — `work_from_bits(block_header.bits)` is added to the fork's cumulative chainwork. Chainwork is the sole criterion for chain reorg (`current_header.chain_work > total_main_chain_chainwork`). [7](#0-6) 

By crafting fork block timestamps so that `time(mainchain_D) < time(fork_D)`, the incorrect calculation produces a longer apparent timespan → lower difficulty (higher `bits`) → the attacker mines fork blocks with less PoW work than the protocol requires. The attacker can then submit a sequence of such under-difficulty fork blocks. If the fork's cumulative chainwork eventually exceeds the mainchain's, `reorg_chain` is triggered, corrupting the canonical chain stored in `mainchain_height_to_header` and `mainchain_header_to_height`, and invalidating all subsequent SPV proofs issued by `verify_transaction_inclusion_v2`.

### Likelihood Explanation

**Likelihood: Medium**

For **Dogecoin** (the most exposed chain), the difficulty adjusts every block after height 145,000. The wrong ancestor is used starting from the **third** fork block (H = D+2). Any relayer-path actor who can call `submit_blocks` with a crafted fork sequence of three or more blocks triggers the condition. No privileged role is required beyond the trusted-relayer check, which can be bypassed by accounts holding `Role::UnrestrictedSubmitBlocks` or `Role::DAO`.

For **Bitcoin** and **Litecoin**, the difficulty window is 2016 blocks, so the fork must diverge more than 2016 blocks back before the wrong ancestor matters — this is unrealistic in normal operation, making those deployments low-likelihood.

### Recommendation

Replace the height-based mainchain lookup with a backwards chain traversal from the fork's `prev_block_header`. Add a helper to `BlocksGetter` (or inline the loop) that walks `get_prev_header` repeatedly until the target height is reached:

```rust
// Instead of:
let first_block_time = blocks_getter.get_header_by_height(height_first).block_header.time;

// Use:
let mut cursor = prev_block_header.clone();
while cursor.block_height > height_first {
    cursor = blocks_getter.get_prev_header(&cursor.block_header);
}
let first_block_time = cursor.block_header.time;
```

This mirrors how the reference Dogecoin/Bitcoin implementations traverse the chain: they walk backwards from the current tip along the actual chain being validated, not from a global height index.

### Proof of Concept

1. Contract is deployed for Dogecoin mainnet (post-block 145,000, `difficulty_adjustment_interval = 1`).
2. Mainchain contains blocks at heights …, D-1, D (timestamp `T_m`), D+1.
3. Attacker calls `submit_blocks` with:
   - Fork block at height D (parent = mainchain D-1, timestamp `T_f` where `T_f >> T_m`, within MTP/future-time limits).
   - Fork block at height D+1 (parent = fork D, timestamp `T_f + 60`).
   - Fork block at height D+2 (parent = fork D+1).
     - `height_first = D`
     - `get_header_by_height(D)` returns **mainchain block at D** with timestamp `T_m`
     - `modulated_timespan` is computed from `T_f+60 - T_m` (large) instead of `T_f+60 - T_f = 60`
     - `calculate_next_work_required` returns a higher `bits` (easier difficulty) than correct
     - Attacker submits fork block D+2 with that inflated `bits`; PoW check passes against the easier target
4. Attacker repeats for D+3, D+4, … accumulating chainwork with under-difficulty blocks.
5. Once fork chainwork exceeds mainchain chainwork, `reorg_chain` executes, replacing the canonical chain. [8](#0-7) [9](#0-8)

### Citations

**File:** contract/src/lib.rs (L549-567)
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

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
```

**File:** contract/src/lib.rs (L664-667)
```rust
    /// Stores and handles fork submissions
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

**File:** contract/src/dogecoin.rs (L23-33)
```rust
    pub(crate) fn check_pow(&self, block_header: &Header, prev_block_header: &ExtendedHeader) {
        let expected_bits =
            get_next_work_required(&self.get_config(), block_header, prev_block_header, self);

        require!(
            expected_bits == block_header.bits,
            format!(
                "Error: Incorrect target. Expected bits: {:?}, Actual bits: {:?}",
                expected_bits, block_header.bits
            )
        );
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

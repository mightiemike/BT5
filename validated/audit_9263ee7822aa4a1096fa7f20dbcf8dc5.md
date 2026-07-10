### Title
Dogecoin Difficulty Retarget Uses Mainchain Ancestor Timestamp Instead of Fork Ancestor, Enabling Incorrect PoW Acceptance on Fork Blocks - (File: contract/src/dogecoin.rs)

### Summary
In the Dogecoin difficulty retarget path, `get_next_work_required` fetches the boundary block's timestamp via `get_header_by_height`, which unconditionally reads from `mainchain_height_to_header`. When a fork block triggers a retarget, the timestamp used is from the mainchain block at that height, not from the fork's actual ancestor. This is explicitly flagged with a `TODO` acknowledging the uncertainty. The result is that `expected_bits` is computed from the wrong timespan, allowing fork blocks with an incorrect (potentially lower) difficulty target to pass `check_pow`.

### Finding Description
`get_next_work_required` in `dogecoin.rs` computes the retarget boundary block's timestamp as:

```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [1](#0-0) 

`get_header_by_height` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [2](#0-1) 

This always returns the **mainchain** block at `height_first`, not the fork's actual ancestor at that height. When a fork diverges before `height_first`, the two chains have different blocks (and thus different timestamps) at that height. The correct implementation would walk the fork's `prev_block_hash` chain back to `height_first`.

The retarget formula in `calculate_next_work_required` computes:

```rust
let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;
``` [3](#0-2) 

A wrong `first_block_time` produces a wrong `modulated_timespan`, which produces a wrong `expected_bits`. The `check_pow` call then enforces this wrong target:

```rust
require!(
    expected_bits == block_header.bits,
    ...
);
``` [4](#0-3) 

### Impact Explanation
An attacker submitting a crafted fork that diverges before a retarget boundary can have the contract compute `expected_bits` using the mainchain's `first_block_time` instead of the fork's. By controlling the fork's timestamps (within the `time-too-old`/`time-too-new` window), the attacker can make the mainchain's `first_block_time` appear larger than the fork's actual ancestor's time, shrinking `modulated_timespan` and producing a lower (easier) `expected_bits`. The contract then accepts fork blocks that satisfy this artificially lowered difficulty, enabling a chain reorg with less cumulative PoW than the protocol requires. This corrupts the canonical chain tip stored in `mainchain_tip_blockhash` and invalidates all subsequent SPV proofs verified against it.

### Likelihood Explanation
The Dogecoin retarget interval is 1 block (post-height 145,000), meaning every block after that height is a retarget boundary. Any fork submission that diverges before the retarget boundary block triggers this path. The entry point is the public `submit_blocks` method, callable by any unprivileged NEAR account (the `#[trusted_relayer]` guard can be bypassed by accounts with `Role::UnrestrictedSubmitBlocks`, and the relayer staking mechanism is a separate layer). The TODO comment confirms the developers themselves identified this as an unresolved correctness question. [5](#0-4) [6](#0-5) 

### Recommendation
Replace the `get_header_by_height` call with an ancestor walk that follows `prev_block_hash` links from `prev_block_header` back `blocks_to_go_back` steps, identical to how `get_median_time_past` traverses the chain in `utils.rs`. This ensures the retarget boundary timestamp is always taken from the block's actual ancestor chain, regardless of what the current mainchain contains at that height. [7](#0-6) 

### Proof of Concept
1. Contract is initialized with Dogecoin mainnet at height ≥ 145,000 (per-block retarget active).
2. Attacker constructs a fork that diverges from the mainchain at height `H - 2` (two blocks before the current tip `H`). The fork ancestor at `height_first = H - 1` has a timestamp `T_fork` that differs from the mainchain block at the same height (`T_main`).
3. Attacker calls `submit_blocks` with the fork block at height `H`. Inside `get_next_work_required`, `height_first = H - 1` and `get_header_by_height(H-1)` returns the **mainchain** block with time `T_main`.
4. `modulated_timespan` is computed as `T_prev_fork - T_main` instead of the correct `T_prev_fork - T_fork`.
5. If `T_main > T_fork`, `modulated_timespan` is smaller, `new_target` is smaller (easier), and `expected_bits` encodes a lower difficulty.
6. The attacker's fork block carries `bits` matching this easier target and a PoW hash satisfying it. `check_pow` passes.
7. If the fork's cumulative `chain_work` exceeds the mainchain's, `reorg_chain` is triggered, replacing the canonical tip with the attacker's fork. [8](#0-7) [9](#0-8)

### Citations

**File:** contract/src/dogecoin.rs (L27-33)
```rust
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

**File:** contract/src/dogecoin.rs (L307-309)
```rust
    let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;

    let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;
```

**File:** contract/src/lib.rs (L166-169)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
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

**File:** contract/src/utils.rs (L10-26)
```rust
pub fn get_median_time_past(
    block_header: ExtendedHeader,
    prev_block_getter: &impl BlocksGetter,
) -> u32 {
    use btc_types::network::MEDIAN_TIME_SPAN;

    let mut median_time = [0u32; MEDIAN_TIME_SPAN];
    let mut current_header = block_header;

    for slot in &mut median_time {
        *slot = current_header.block_header.time;
        current_header = prev_block_getter.get_prev_header(&current_header.block_header);
    }

    median_time.sort_unstable();
    median_time[median_time.len() / 2]
}
```

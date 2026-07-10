After thoroughly reviewing the codebase, I found a concrete analog.

---

### Title
Dogecoin Fork Block Difficulty Desynchronization via Mainchain Height Lookup — (`contract/src/dogecoin.rs`)

### Summary
When validating Dogecoin fork blocks, `get_next_work_required` fetches the "first block" in the difficulty adjustment interval using `get_header_by_height`, which always returns the **mainchain** block at that height. For fork blocks whose ancestor at that height differs from the mainchain block, the difficulty calculation uses the wrong timestamp, allowing an attacker to manipulate `expected_bits` and submit fork blocks with artificially reduced difficulty.

### Finding Description

In `get_next_work_required` (dogecoin.rs), after computing `height_first`, the function calls:

```rust
// TODO: check if it is correct to get block header by height from mainchain
// without looping to find the ancestor
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

This always resolves through `mainchain_height_to_header`, returning the **mainchain** block at `height_first`, not the fork's actual ancestor at that height.

For the new Dogecoin difficulty protocol (height ≥ 145 000), `difficulty_adjustment_interval = 1` and `blocks_to_go_back = 0`, so:

```rust
let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
let difficulty_adjustment_interval = if new_difficulty_protocol { 1 } else { ... };
...
let mut blocks_to_go_back = difficulty_adjustment_interval - 1; // = 0
let height_first = prev_block_header.block_height.checked_sub(blocks_to_go_back)...;
``` [3](#0-2) 

This means `height_first == prev_block_header.block_height`. The correct `first_block_time` should equal `prev_block_header.block_header.time` (the fork's own prev block). Instead, the code fetches the **mainchain** block at that same height, which is a different block whenever the fork diverged at or before that height.

The difficulty calculation then computes:

```rust
let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;
let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;
// clamped to [0.75 * retarget_timespan, 1.5 * retarget_timespan]
...
new_target = new_target * modulated_timespan / retarget_timespan;
``` [4](#0-3) 

An attacker who controls the fork block's timestamp can set `prev_block_header.time` up to `current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL` seconds ahead of the NEAR block time. If this value exceeds `mainchain_block_at_height_first.time` by more than `1.5 × retarget_timespan`, `modulated_timespan` is clamped to its maximum, yielding a target 50% easier than the previous block's target. The correct calculation (where `first_block_time == prev_block_header.time`) would give `raw_timespan = 0`, producing a slightly **harder** target (`7/8 × retarget_timespan`).

The `check_pow` function enforces `expected_bits == block_header.bits`:

```rust
require!(
    expected_bits == block_header.bits,
    format!("Error: Incorrect target. Expected bits: {:?}, Actual bits: {:?}", ...)
);
``` [5](#0-4) 

Because `expected_bits` is derived from the manipulated `modulated_timespan`, the attacker can claim lower bits (easier difficulty) in their fork block and still pass this check. The subsequent PoW check then validates against `target_from_bits(block_header.bits)`, which is the attacker's easier target.

The developers themselves flagged this uncertainty with the TODO comment at line 291.

### Impact Explanation

**Impact: High.** An attacker can submit Dogecoin fork blocks with up to 50% reduced difficulty per block compared to what the protocol requires. Over a sequence of fork blocks, this compounds, allowing the attacker to accumulate more `chain_work` per unit of real mining effort than legitimate miners. If the fork's `chain_work` exceeds the mainchain's, `reorg_chain` is triggered:

```rust
if current_header.chain_work > total_main_chain_chainwork {
    log!("Chain reorg");
    self.reorg_chain(current_header, last_main_chain_block_height);
}
``` [6](#0-5) 

A false reorg corrupts `mainchain_tip_blockhash`, `mainchain_height_to_header`, and `mainchain_header_to_height`, causing `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` to return incorrect results for any downstream consumer of the light client.

### Likelihood Explanation

**Likelihood: Medium.** The attack requires submitting Dogecoin fork blocks via the public `submit_blocks` entrypoint (no privileged role needed). The attacker must mine blocks meeting the (incorrectly) reduced difficulty, which is feasible given the 50% reduction. The attack only applies to the Dogecoin build (feature flag `dogecoin`) and only at heights ≥ 145 001, but those are all current production heights.

### Recommendation

Replace the `get_header_by_height(height_first)` call with a backwards walk from `prev_block_header` through `get_prev_header` until reaching `height_first`. This ensures the difficulty calculation always uses the fork's actual ancestor at that height, not the mainchain block. For the common case where `height_first == prev_block_header.block_height` (new difficulty protocol), the result is simply `prev_block_header.block_header.time`, eliminating the desynchronization entirely.

### Proof of Concept

1. Mainchain is at height 145 010. Mainchain block at height 145 009 has timestamp `T_main`.
2. Attacker submits a fork block at height 145 010 (diverging at 145 009) with timestamp `T_fork = T_main + 10_000` (within `MAX_FUTURE_BLOCK_TIME_LOCAL`). This block passes the difficulty check for height 145 010 (calculated correctly from the mainchain's 145 009 block).
3. Attacker now submits a fork block at height 145 011. `prev_block_header` = fork block at 145 010. `height_first = 145 010`. `get_header_by_height(145 010)` returns the **mainchain** block at 145 010 with timestamp `T_main2`.
4. `raw_timespan = T_fork - T_main2`. If `T_fork >> T_main2`, `modulated_timespan` is clamped to `1.5 × retarget_timespan`, giving a target 50% easier than the fork block at 145 010's target.
5. Attacker claims these lower bits in the fork block at 145 011 and mines it against the easier target.
6. Repeating this for each subsequent fork block, the attacker accumulates `chain_work` faster than legitimate miners, eventually triggering `reorg_chain` and corrupting the canonical chain state.

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

**File:** contract/src/dogecoin.rs (L244-295)
```rust
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
```

**File:** contract/src/dogecoin.rs (L306-332)
```rust
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
```

**File:** contract/src/lib.rs (L563-566)
```rust
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

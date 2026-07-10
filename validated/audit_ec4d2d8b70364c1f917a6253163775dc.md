### Title
Fork Difficulty Calculated Against Mainchain Ancestor Instead of Fork Ancestor, Allowing Difficulty Bypass - (File: `contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`)

### Summary

`get_header_by_height` always reads from the `mainchain_height_to_header` mapping. When a fork block is being validated at a difficulty-adjustment boundary, the difficulty calculation fetches the period's first block from the **mainchain** rather than from the fork's actual ancestor chain. An attacker can craft fork headers whose timestamps make the mainchain-derived timespan produce a lower difficulty (higher target) than the fork's real timestamps would require, allowing fork blocks to pass the PoW difficulty check with less work than the protocol mandates.

### Finding Description

`get_header_by_height` in `contract/src/lib.rs` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header          // always the current mainchain
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [1](#0-0) 

This function is called during difficulty validation for fork blocks in all three non-Zcash chain implementations.

**Dogecoin** (`dogecoin.rs`, post-height 145,000, `difficulty_adjustment_interval = 1`): every block recalculates difficulty. When validating fork block B[k+1] whose parent is B[k] at height k, the code computes `height_first = k - 1` and calls `get_header_by_height(k-1)`, returning the **mainchain** block A[k-1] rather than the fork's actual ancestor B[k-1]:

```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [2](#0-1) 

The `calculate_next_work_required` then computes:

```
modulated_timespan = B[k].time − A[k-1].time   ← uses mainchain block
```

but the protocol-correct computation is:

```
modulated_timespan = B[k].time − B[k-1].time   ← should use fork ancestor
``` [3](#0-2) 

**Bitcoin** (`bitcoin.rs`) and **Litecoin** (`litecoin.rs`) have the identical structural flaw at every 2016-block difficulty adjustment boundary:

```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(
    config,
    prev_block_header,
    interval_tail_extend_header.block_header.time.into(),
)
``` [4](#0-3) [5](#0-4) 

The TODO comment in `dogecoin.rs` explicitly acknowledges the concern but leaves it unresolved.

### Impact Explanation

An attacker who submits a fork diverging at height k-1 (or earlier) can set B[k-1].time to be arbitrarily close to B[k].time. The correct timespan `B[k].time − B[k-1].time` would be very small, which under Digishield/retarget rules would **increase** difficulty (lower target). However, the contract computes the timespan using A[k-1].time (the mainchain block), which has a normal elapsed time from B[k].time, yielding a **lower** difficulty (higher target) for B[k+1]. The fork block B[k+1] is then accepted with a `bits` value that encodes this artificially relaxed target, passing the PoW hash check with less work than the protocol requires. If the attacker accumulates enough such blocks, the fork's `chain_work` can exceed the mainchain's, triggering `reorg_chain` and corrupting the light client's canonical chain mapping — invalidating all subsequent `verify_transaction_inclusion` results. [6](#0-5) 

### Likelihood Explanation

The trigger is reachable by any unprivileged NEAR caller via `submit_blocks`. For Dogecoin (interval=1), the flaw fires on every fork block after the second one — no special timing is required. For Bitcoin/Litecoin (interval=2016), the flaw fires only at retarget boundaries, but those occur predictably every ~2 weeks and a fork submitted across one boundary is sufficient. No privileged role, leaked key, or social engineering is needed. [7](#0-6) 

### Recommendation

Replace `get_header_by_height` calls inside difficulty calculation with ancestor traversal that walks the fork's own `prev_block_hash` chain back to the required height. Concretely, starting from `prev_block_header`, call `get_prev_header` in a loop `blocks_to_go_back` times. This mirrors how Zcash's difficulty function already correctly traverses ancestors via `get_prev_header` rather than a height-indexed mainchain lookup. [8](#0-7) 

### Proof of Concept

**Dogecoin mainnet, post-height 145,000 (interval = 1):**

1. Mainchain has blocks A[k-2], A[k-1], A[k] with timestamps spaced ~60 s apart.
2. Attacker submits fork block B[k-1] (diverges from A[k-2]) with `B[k-1].time = A[k].time − 1` (nearly identical to A[k].time).
3. Attacker submits fork block B[k] with `B[k].time = A[k].time`.
4. When validating B[k+1], the contract calls `get_header_by_height(k-1)` → returns A[k-1] (mainchain). Computed timespan = `B[k].time − A[k-1].time ≈ 60 s` → normal difficulty.
5. Correct timespan = `B[k].time − B[k-1].time = 1 s` → Digishield would clamp and raise difficulty significantly.
6. B[k+1] is accepted with the artificially low difficulty. Repeating this pattern across many blocks lets the attacker accumulate chainwork cheaply and trigger `reorg_chain`. [9](#0-8) [10](#0-9)

### Citations

**File:** contract/src/lib.rs (L169-179)
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

**File:** contract/src/dogecoin.rs (L244-297)
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

    calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
```

**File:** contract/src/dogecoin.rs (L307-309)
```rust
    let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;

    let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;
```

**File:** contract/src/bitcoin.rs (L81-87)
```rust
    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
}
```

**File:** contract/src/litecoin.rs (L88-93)
```rust
    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
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

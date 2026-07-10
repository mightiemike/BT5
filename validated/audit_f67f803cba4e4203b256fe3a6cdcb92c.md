### Title
Fork Retarget Validation Uses Mainchain Height Index Instead of Fork Branch — (`contract/src/lib.rs::get_header_by_height` + `contract/src/litecoin.rs::get_next_work_required`)

---

### Summary

`get_header_by_height` unconditionally resolves block heights through `mainchain_height_to_header`. When `get_next_work_required` calls it to fetch the interval-tail block for a retarget computation on a fork, it silently returns the **mainchain** block at that height instead of the fork's own block. If the fork diverges before the retarget lookback point, the contract computes the expected `bits` from the wrong timestamp, allowing a fork whose `bits` would be rejected by the real Litecoin network to pass on-chain validation.

---

### Finding Description

**Root cause — `get_header_by_height` is mainchain-only:** [1](#0-0) 

The function resolves height → hash exclusively through `mainchain_height_to_header`. Fork blocks are stored only in `headers_pool` via `store_fork_header`, which never writes to the height index: [2](#0-1) 

**Where the wrong block is consumed — `get_next_work_required`:**

At every Litecoin retarget boundary (`(height) % 2016 == 0`), the function computes `first_block_height = prev_height − 2016` and fetches the interval-tail block: [3](#0-2) 

That `get_header_by_height` call returns the **mainchain** block at `first_block_height`, regardless of which branch is currently being validated.

**When the discrepancy is reachable:**

If the fork diverges at height F where `F ≤ first_block_height`, the fork has its own block at `first_block_height` (different `time` field, stored in `headers_pool` but not in `mainchain_height_to_header`). The contract uses the mainchain block's timestamp for `calculate_next_work_required`, producing a different `expected_bits` than the real Litecoin network would compute for that fork branch. [4](#0-3) 

**The check that should catch this passes instead:**

```rust
require!(
    expected_bits == block_header.bits,
    "bad-diffbits: incorrect proof of work"
);
``` [5](#0-4) 

The attacker sets `bits` in the fork's retarget block to match the **mainchain-derived** `expected_bits`. The contract accepts it; the real Litecoin network would reject it because the correct computation uses the fork's own interval-tail timestamp.

---

### Impact Explanation

A fork that passes the contract's `check_pow` / `get_next_work_required` gate but carries an incorrect `bits` value can be stored as the canonical chain after a reorg (`reorg_chain`). Any downstream bridge or proof-verification consumer that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against that fork will treat confirmations on an invalid branch as final — a direct light-client verification bypass. [6](#0-5) 

---

### Likelihood Explanation

The entry point is `submit_blocks`, which is gated by `#[trusted_relayer]`. [7](#0-6) 

An unprivileged account cannot call it directly. However, the production relayer is a trusted entity that faithfully relays whatever headers appear on the Litecoin network. An attacker with sufficient Litecoin mining power can publish a real fork whose headers satisfy all scrypt PoW checks and whose `bits` at the retarget boundary are crafted to match the mainchain-derived computation. The production relayer submits those headers without modification; the contract accepts them. No contract-level privilege is required — only mining capability on the source chain, which is the standard threat model for a light-client bridge.

---

### Recommendation

Replace the height-based mainchain lookup in `get_header_by_height` with a branch-aware traversal when called during fork validation. Concretely, `get_next_work_required` should walk backwards through `headers_pool` via `prev_block_hash` links from `prev_block_header` until it reaches `first_block_height`, rather than calling `get_header_by_height`. This mirrors how `get_median_time_past` already correctly traverses the chain via `get_prev_header`: [8](#0-7) 

The same parent-link traversal pattern should be applied in `get_next_work_required` to fetch the interval-tail block from the correct branch.

---

### Proof of Concept

1. Initialize the contract with Litecoin mainchain blocks covering heights `[2015 .. 4031]` (two full 2016-block periods). Mainchain block at height 2015 has timestamp `T_main`.

2. Construct a fork diverging at height 2014 (before `first_block_height = 4031 − 2016 = 2015`). The fork's block at height 2015 has timestamp `T_fork ≠ T_main`.

3. Submit fork blocks 2015 → 4030 via `submit_blocks` (relayer-signed). Each is stored via `store_fork_header` — none enter `mainchain_height_to_header`.

4. Compute what `calculate_next_work_required` produces using `T_main` (mainchain timestamp). Call this `bits_mainchain`.

5. Craft fork block at height 4032 with `bits = bits_mainchain`. This differs from what the real Litecoin network would require for this fork (which uses `T_fork`).

6. Submit fork block 4032. `get_next_work_required` calls `get_header_by_height(2015)`, returns the mainchain block (timestamp `T_main`), computes `expected_bits = bits_mainchain`, and the `require!(expected_bits == block_header.bits)` check passes.

7. If the fork's chainwork exceeds the mainchain's, `reorg_chain` promotes it. The contract now stores a fork with an incorrect `bits` value as canonical. Downstream `verify_transaction_inclusion` calls on this branch return `true` for transactions that the real Litecoin network would never confirm. [9](#0-8) [1](#0-0)

### Citations

**File:** contract/src/lib.rs (L167-169)
```rust
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L560-566)
```rust
            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
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

**File:** contract/src/litecoin.rs (L23-27)
```rust
        // Check proof of work
        require!(
            expected_bits == block_header.bits,
            "bad-diffbits: incorrect proof of work"
        );
```

**File:** contract/src/litecoin.rs (L51-94)
```rust
fn get_next_work_required(
    config: &NetworkConfig,
    block_header: &Header,
    prev_block_header: &ExtendedHeader,
    blocks_getter: &impl BlocksGetter,
) -> u32 {
    if (prev_block_header.block_height + 1) % config.difficulty_adjustment_interval != 0 {
        if config.pow_allow_min_difficulty_blocks {
            if block_header.time
                > prev_block_header.block_header.time + 2 * config.pow_target_spacing
            {
                return config.proof_of_work_limit_bits;
            }

            let mut current_block_header = prev_block_header.clone();
            while current_block_header.block_header.bits == config.proof_of_work_limit_bits
                && current_block_header.block_height % config.difficulty_adjustment_interval != 0
            {
                current_block_header =
                    blocks_getter.get_prev_header(&current_block_header.block_header);
            }

            let last_bits = current_block_header.block_header.bits;
            return last_bits;
        }
        return prev_block_header.block_header.bits;
    }

    // Litecoin: This fixes an issue where a 51% attack can change difficulty at will.
    // Go back the full period unless it's the first retarget after genesis. Code courtesy of Art Forz
    let mut blocks_to_go_back = config.difficulty_adjustment_interval - 1;
    if prev_block_header.block_height + 1 != config.difficulty_adjustment_interval {
        blocks_to_go_back = config.difficulty_adjustment_interval;
    }

    let first_block_height = prev_block_header.block_height - blocks_to_go_back;

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
}
```

**File:** contract/src/litecoin.rs (L97-134)
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

    let mut new_target = target_from_bits(prev_block_header.block_header.bits);

    let shift: bool = new_target.bits() > config.pow_limit.bits() - 1;
    if shift {
        new_target = new_target >> 1;
    }

    let (mut new_target, new_target_overflow) =
        new_target.overflowing_mul(<i64 as TryInto<u64>>::try_into(actual_time_taken).unwrap());
    require!(!new_target_overflow, "new target overflow");
    new_target = new_target
        / U256::from(<i64 as TryInto<u64>>::try_into(config.pow_target_timespan).unwrap());

    if shift {
        new_target = new_target << 1;
    }

    if new_target > config.pow_limit {
        new_target = config.pow_limit;
    }

    new_target.target_to_bits()
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

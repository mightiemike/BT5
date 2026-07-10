### Title
`get_header_by_height` Uses Mainchain-Only Lookup for Fork Difficulty Calculation, Enabling Difficulty Manipulation - (`contract/src/dogecoin.rs`)

### Summary

The Dogecoin (and Litecoin/Bitcoin) difficulty-retarget logic fetches the interval-boundary block via `get_header_by_height`, which resolves exclusively from the **mainchain** height-to-hash map. When a fork block is being validated, this returns the mainchain block at that height rather than the fork's actual ancestor. An unprivileged relayer/proof-submitter can craft fork block timestamps to exploit the mismatch, causing the contract to accept fork blocks whose `bits` field encodes a lower difficulty than the protocol requires, corrupting the `chain_work` accumulation that drives fork-choice.

---

### Finding Description

`get_header_by_height` is implemented in `contract/src/lib.rs` as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header          // ← mainchain-only map
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [1](#0-0) 

It reads from `mainchain_height_to_header`, which only contains blocks on the canonical chain. Fork blocks are stored only in `headers_pool` and are never inserted into `mainchain_height_to_header`. [2](#0-1) 

In `contract/src/dogecoin.rs`, `get_next_work_required` calls this function to obtain the timestamp of the interval-boundary block used in the DigiShield retarget formula. The developer explicitly flagged the problem with a TODO comment:

```rust
// TODO: check if it is correct to get block header by height from mainchain
// without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [3](#0-2) 

The same pattern appears in `contract/src/litecoin.rs` and `contract/src/bitcoin.rs`: [4](#0-3) [5](#0-4) 

**Dogecoin is the most critical case.** Post-block 145,000, Dogecoin uses `difficulty_adjustment_interval = 1`, so `blocks_to_go_back = 1` for every block after genesis: [6](#0-5) 

This means `height_first = prev_block_height - 1`. For any fork that diverges at or before `prev_block_height - 1`, the mainchain block at `height_first` is a **different block** from the fork's actual ancestor at that height. The DigiShield formula then computes:

```
modulated_timespan = retarget_timespan + (fork_prev.time - mainchain_block_at(height_first).time - retarget_timespan) / 8
```

where `mainchain_block_at(height_first).time` is substituted for the fork ancestor's timestamp. The attacker controls `fork_prev.time` (within MTP and future-time bounds) and can therefore drive `modulated_timespan` toward `min_timespan`, yielding a lower required difficulty. [7](#0-6) 

---

### Impact Explanation

The `bits` field of each submitted fork block is validated against the value returned by `get_next_work_required`. If that function returns a lower `bits` value (easier target) than the protocol requires, the contract accepts the block and accumulates `work_from_bits(block_header.bits)` into `chain_work`: [8](#0-7) 

A fork whose blocks carry artificially low difficulty accumulates less `chain_work` per block than a legitimately mined chain. However, the fork-choice rule promotes a fork when `current_header.chain_work > total_main_chain_chainwork`: [9](#0-8) 

An attacker who can mine blocks at the reduced difficulty can build a fork that the contract accepts as valid (each block passes `check_pow`) while the actual cumulative work is lower than the mainchain's. If the attacker mines enough such blocks, the fork's `chain_work` can exceed the mainchain's, triggering a reorg. Downstream consumers of `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` would then verify transactions against a fraudulent canonical chain.

---

### Likelihood Explanation

Dogecoin's per-block retarget (post-145,000) means the vulnerable code path is exercised on **every single fork block submission** where the fork diverges more than one block back. The entry point is the public `submit_blocks` call, reachable by any unprivileged NEAR account. The attacker only needs to:

1. Submit a fork that diverges ≥ 2 blocks behind the current tip (normal relayer behavior during any reorg).
2. Craft fork block timestamps to be just above MTP of the fork's own ancestors but well below the mainchain block's timestamp at `height_first`.
3. Mine fork blocks at the resulting reduced difficulty.

DigiShield's clamping limits the per-block difficulty reduction to roughly 1.875% (from `min_timespan = 45` vs `retarget_timespan = 60`), but this compounds across every fork block, making a sustained attack feasible.

---

### Recommendation

Replace the `get_header_by_height` call in all three retarget functions with an ancestor walk that follows `prev_block_hash` links through `headers_pool` starting from `prev_block_header`, until reaching the block at `height_first`. This mirrors how Bitcoin Core's `GetAncestor` works and ensures the boundary block is always the fork's true ancestor regardless of mainchain state.

---

### Proof of Concept

1. Mainchain has blocks at heights H-2 (timestamp `T_mc`) and H-1 (timestamp `T_mc + 60`).
2. Attacker submits a fork diverging at H-2: fork block at H-2 has timestamp `T_fork_early` (e.g., `T_mc - 300`, satisfying MTP of its own ancestors).
3. Attacker submits fork block at H-1 with timestamp `T_fork_prev = T_mc - 240` (satisfying MTP of fork's H-2 block, and within 2-hour future bound).
4. Contract calls `get_next_work_required` for fork block at H:
   - `height_first = H - 2`
   - `get_header_by_height(H-2)` returns **mainchain** block with timestamp `T_mc`
   - `modulated_timespan = 60 + ((T_fork_prev - T_mc) - 60) / 8 = 60 + ((-240 - 60)) / 8 = 60 - 37.5 = 22.5` → clamped to `min_timespan = 45`
   - New target = `prev_target * 45 / 60` → **25% easier** than correct difficulty
5. Attacker mines fork block at H against this reduced target, passes `check_pow`, and the block is accepted with inflated `chain_work` relative to actual work performed.
6. Repeated across many blocks, the fork accumulates enough `chain_work` to trigger `reorg_chain`, replacing the canonical chain with the attacker's low-work fork. [10](#0-9) [11](#0-10)

### Citations

**File:** contract/src/lib.rs (L563-565)
```rust
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
```

**File:** contract/src/lib.rs (L664-667)
```rust
    /// Stores and handles fork submissions
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
    }
```

**File:** contract/src/lib.rs (L670-682)
```rust
impl BlocksGetter for BtcLightClient {
    fn get_prev_header(&self, current_header: &LightHeader) -> ExtendedHeader {
        self.headers_pool
            .get(&current_header.prev_block_hash)
            .unwrap_or_else(|| env::panic_str("PrevBlockNotFound"))
    }

    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```

**File:** contract/src/dogecoin.rs (L191-194)
```rust
        let (current_block_computed_chain_work, overflow) = prev_block_header
            .chain_work
            .overflowing_add(work_from_bits(block_header.bits));
        require!(!overflow, "Addition of U256 values overflowed");
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

**File:** contract/src/dogecoin.rs (L307-316)
```rust
    let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;

    let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;

    let min_timespan = retarget_timespan - (retarget_timespan / 4);
    let max_timespan = retarget_timespan + (retarget_timespan / 2);

    if modulated_timespan < min_timespan {
        modulated_timespan = min_timespan;
    } else if modulated_timespan > max_timespan {
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

**File:** btc-types/src/network.rs (L82-84)
```rust
        Network::Mainnet => DogecoinConfig {
            difficulty_adjustment_interval: 1,
            pow_target_timespan: 60,
```

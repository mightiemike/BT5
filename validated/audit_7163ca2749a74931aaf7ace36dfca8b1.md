### Title
Fork Block Difficulty Validation Uses Mainchain Ancestor Instead of Fork's Own Ancestor - (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`)

### Summary

When computing the expected difficulty for a submitted block, `get_next_work_required` calls `get_header_by_height` to look up the retarget-interval boundary block. That helper always reads from `mainchain_height_to_header`, so for any fork block whose retarget-boundary ancestor diverges from the mainchain, the difficulty check silently uses the wrong block's timestamp. The developers themselves flagged this with a `TODO` comment. The analog to M-10 is exact: a validation check that is correct for the "new chain" case is applied unchanged to the "continuation / fork" case without accounting for the already-accumulated fork state.

---

### Finding Description

`get_header_by_height` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header          // ← always mainchain
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [1](#0-0) 

Fork blocks are stored only in `headers_pool` via `store_fork_header`, never in `mainchain_height_to_header`:

```rust
fn store_fork_header(&mut self, header: &ExtendedHeader) {
    self.headers_pool.insert(&header.block_hash, header);
}
``` [2](#0-1) 

Every chain variant calls `get_header_by_height` inside `get_next_work_required` to obtain the timestamp of the first block in the retarget window:

**Dogecoin** (most impactful — per-block retarget after height 145 000):

```rust
// TODO: check if it is correct to get block header by height from mainchain
//       without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [3](#0-2) 

**Bitcoin / Litecoin** (2 016-block retarget):

```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(
    config,
    prev_block_header,
    interval_tail_extend_header.block_header.time.into(),
)
``` [4](#0-3) [5](#0-4) 

For Dogecoin with the post-145 000 protocol, `difficulty_adjustment_interval = 1`, so `blocks_to_go_back = 1` for every block after genesis, and `height_first = prev_block_header.block_height - 1`:

```rust
let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
let difficulty_adjustment_interval = if new_difficulty_protocol { 1 } else { ... };
...
let mut blocks_to_go_back = difficulty_adjustment_interval - 1;
if prev_block_header.block_height + 1 != difficulty_adjustment_interval {
    blocks_to_go_back = difficulty_adjustment_interval;
}
let height_first = prev_block_header.block_height
    .checked_sub(blocks_to_go_back)...;
``` [6](#0-5) 

Consider a fork that diverges at mainchain height `X`. The fork's blocks at heights `X+1`, `X+2`, … are stored only in `headers_pool`. When the contract validates the fork block at height `X+3`:

- `prev_block_header` = fork block at `X+2`
- `height_first` = `X+1`
- `get_header_by_height(X+1)` → returns the **mainchain** block at `X+1`, not the fork's block at `X+1`

The timestamp used in `calculate_next_work_required` is therefore wrong for every fork block at depth ≥ 3. The broken invariant is: *the difficulty target accepted for a fork block is computed from a different chain's history than the block being validated*.

---

### Impact Explanation

`calculate_next_work_required` for Dogecoin computes:

```rust
let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;
``` [7](#0-6) 

`first_block_time` is the mainchain block's timestamp at `height_first`, not the fork's. An attacker who controls the fork block timestamps (within MTP and future-time bounds) can widen or narrow this difference relative to what the correct fork ancestor would produce. A wider difference yields a lower (easier) target; a narrower difference yields a higher (harder) target.

**Concrete security impact:** The attacker can craft fork blocks whose timestamps make the mainchain-derived `first_block_time` appear much earlier than the fork's actual ancestor timestamp. This inflates `modulated_timespan`, lowers the computed difficulty target, and allows the attacker to satisfy the PoW check with less real work than the protocol requires. Over a sustained fork, this lets an attacker accumulate chain-work faster than honest miners, eventually triggering `reorg_chain` and replacing the canonical chain with an attacker-controlled one.

Additionally, `allow_min_difficulty_for_block` compares the fork block's timestamp against the **mainchain** previous block's timestamp (via `prev_block_header`, which is the fork's block), but the retarget calculation uses the mainchain's `height_first` block — creating a further inconsistency that can be exploited to trigger minimum-difficulty acceptance.

---

### Likelihood Explanation

- **Dogecoin build:** Every fork block at depth ≥ 3 is affected. Any account that stakes to become a trusted relayer (the staking mechanism is managed by `RelayerManager` but the staking itself is open) can submit fork blocks via `submit_blocks`. No privileged key is required beyond the staking deposit.
- **Bitcoin / Litecoin builds:** The bug manifests only when the fork spans a 2 016-block retarget boundary, which requires a very deep fork. Likelihood is low for these chains.
- The developers' own `TODO` comment confirms awareness of the incorrect assumption.

---

### Recommendation

Replace `get_header_by_height` (mainchain lookup) with an ancestor traversal that walks the fork's own chain via `get_prev_header` until the target height is reached. Concretely, inside `get_next_work_required`, instead of:

```rust
let first_block_time = blocks_getter.get_header_by_height(height_first).block_header.time;
```

traverse backwards from `prev_block_header` using `get_prev_header` until `block_height == height_first`, ensuring the timestamp belongs to the block that is actually an ancestor of the candidate block being validated.

---

### Proof of Concept

**Setup:** Dogecoin build, mainchain at height 145 010. Mainchain block at height 145 009 has timestamp `T_main`.

1. Attacker submits a fork block at height 145 008 (fork point). Fork block at 145 008 has timestamp `T_fork_base`.
2. Attacker submits fork block at 145 009 with timestamp `T_fork_1` (valid MTP / future-time checks pass).
3. Attacker submits fork block at 145 010 with timestamp `T_fork_2`.
   - `prev_block_header` = fork block at 145 009
   - `height_first` = 145 008
   - `get_header_by_height(145 008)` → **mainchain** block at 145 008, timestamp `T_main_base`
   - `modulated_timespan` = `T_fork_2 - T_main_base` (uses mainchain timestamp, not fork's)
   - If `T_main_base` ≪ `T_fork_2`, the computed target is much easier than the correct one
4. Attacker mines fork block at 145 010 against the artificially lowered target and submits it.
5. The contract accepts it. Repeating this across subsequent blocks accumulates chain-work faster than honest miners, eventually triggering `reorg_chain`. [8](#0-7) [9](#0-8)

### Citations

**File:** contract/src/lib.rs (L562-567)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
```

**File:** contract/src/lib.rs (L665-667)
```rust
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
    }
```

**File:** contract/src/lib.rs (L677-683)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
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

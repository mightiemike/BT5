### Title
Fork Difficulty Validation Uses Mainchain Ancestor Timestamp Instead of Fork Ancestor Timestamp — (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`)

---

### Summary

When validating the proof-of-work difficulty for a fork block at a retarget boundary, `get_header_by_height()` always fetches the block at the required height from the **mainchain index** (`mainchain_height_to_header`), not from the fork's own ancestry. If the fork diverged before that height, the timestamp used to compute the expected difficulty belongs to a different (mainchain) block, not the fork's actual ancestor. This produces an incorrect expected `bits` value, allowing fork blocks with wrong difficulty to pass validation or causing valid fork blocks to be rejected. The Dogecoin build (post-block 145,000) is the most severely affected because every single block is a retarget boundary, making the bug reachable with any fork of depth ≥ 3.

---

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

It exclusively consults `mainchain_height_to_header`, which only maps heights to **mainchain** block hashes. Fork blocks are stored in `headers_pool` but are never inserted into `mainchain_height_to_header`. [2](#0-1) 

During difficulty adjustment for a fork block, all three chain implementations call `get_header_by_height` to retrieve the timestamp of the block at the start of the retarget window:

**Dogecoin** (post-145,000, every block is a retarget):

```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [3](#0-2) 

The developers themselves flagged this with a `TODO` comment acknowledging the potential incorrectness.

**Bitcoin:**

```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(config, prev_block_header, interval_tail_extend_header.block_header.time.into())
``` [4](#0-3) 

**Litecoin:**

```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(config, prev_block_header, interval_tail_extend_header.block_header.time.into())
``` [5](#0-4) 

When a fork block F_n is being validated and the fork diverged at or before `height_first`, the fork's actual ancestor at `height_first` is a fork block (stored only in `headers_pool`, not in `mainchain_height_to_header`). The call to `get_header_by_height(height_first)` silently returns the **mainchain** block at that height instead. The timestamp from this wrong block is fed into `calculate_next_work_required`, producing an incorrect expected `bits` value.

This is structurally identical to the reported `EscrowManager.getPastVotes()` bug: a **current/present-state value** (the mainchain block's timestamp at a given height) is used to reconstruct a **historical value** (the fork's ancestor's timestamp at that height), and the two diverge whenever the fork's history differs from the mainchain's history at that point.

---

### Impact Explanation

The incorrect timestamp changes the computed `actual_time_taken` (or `modulated_timespan` for Dogecoin), which directly determines the expected `bits` for the fork block: [6](#0-5) 

Two concrete consequences:

1. **Invalid fork block accepted**: An attacker crafts a fork block whose `bits` field matches the difficulty computed from the *mainchain* ancestor's timestamp rather than the correct fork ancestor's timestamp. The contract accepts it as valid PoW. If the fork's cumulative `chain_work` then exceeds the mainchain tip's `chain_work`, `reorg_chain` is triggered, replacing the mainchain with a chain that passed an incorrect difficulty check. [7](#0-6) 

2. **Valid fork block rejected**: A legitimately mined fork block with the correct `bits` for its own ancestry is rejected because the contract computes the expected difficulty using the wrong timestamp.

The corrupted invariant is: **the `bits` field accepted for a fork block at a retarget boundary does not correspond to the actual PoW difficulty required by the fork's own chain history**. This breaks the core security property of the SPV light client — that only blocks with valid PoW are accepted — and can cause `verify_transaction_inclusion` to return `true` for transactions in a fraudulently constructed fork.

---

### Likelihood Explanation

**Dogecoin (high likelihood):** Post-block 145,000, `difficulty_adjustment_interval = 1`, so every block is a retarget boundary and `height_first = prev_block_header.block_height - 1`. [8](#0-7) 

Any fork of depth ≥ 3 from the divergence point triggers the bug. The attacker submits F1 and F2 as fork blocks; when F3 is submitted, `height_first` points to F1's height, which is a fork block not in `mainchain_height_to_header`, so the mainchain block at that height is used instead. This is trivially reachable via the public `submit_blocks` endpoint.

**Bitcoin / Litecoin (low likelihood):** The retarget interval is 2,016 blocks, so the fork must be at least 2,016 blocks deep before the bug triggers. This is impractical under normal network conditions but remains a latent correctness defect.

---

### Recommendation

Replace `get_header_by_height` (mainchain-only lookup) with an ancestor-traversal function that walks the fork's `prev_block_hash` chain back to `height_first`. Since fork blocks are stored in `headers_pool` with their `prev_block_hash` intact, a loop starting from `prev_block_header` and calling `get_prev_header` until the target height is reached will always return the correct fork ancestor regardless of whether it is on the mainchain.

The existing `get_prev_header` already performs this correctly for single-step lookups: [9](#0-8) 

A helper that iterates `get_prev_header` down to the target height should replace all three `get_header_by_height` calls in the retarget logic.

---

### Proof of Concept

**Setup (Dogecoin, post-145,000):**

1. Mainchain contains blocks at heights H, H+1, H+2 with timestamps `T_m_H`, `T_m_H1`, `T_m_H2`.
2. Attacker calls `submit_blocks` with fork block F1 at height H+1 (parent = mainchain block at H), timestamp `T_f1 ≠ T_m_H1`.
3. Attacker calls `submit_blocks` with fork block F2 at height H+2, timestamp `T_f2`.
4. Attacker calls `submit_blocks` with fork block F3 at height H+3.

**During F3 validation:**
- `prev_block_header` = F2 at height H+2
- `height_first = H+2 - 1 = H+1`
- `get_header_by_height(H+1)` → returns **mainchain block at H+1** with timestamp `T_m_H1`
- Correct value should be **F1** with timestamp `T_f1`
- `calculate_next_work_required` uses `T_m_H1` instead of `T_f1`

**Result:** The expected `bits` for F3 is computed from `T_m_H1 - T_f2` instead of the correct `T_f1 - T_f2`. The attacker sets F3's `bits` to match the incorrect calculation. If F3's `chain_work` exceeds the mainchain tip, `reorg_chain` promotes the fork, and subsequent `verify_transaction_inclusion` calls operate on a chain whose difficulty was not correctly enforced. [10](#0-9) [11](#0-10)

### Citations

**File:** contract/src/lib.rs (L562-566)
```rust
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

**File:** contract/src/lib.rs (L671-675)
```rust
    fn get_prev_header(&self, current_header: &LightHeader) -> ExtendedHeader {
        self.headers_pool
            .get(&current_header.prev_block_hash)
            .unwrap_or_else(|| env::panic_str("PrevBlockNotFound"))
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

**File:** contract/src/dogecoin.rs (L244-249)
```rust
    let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
    let difficulty_adjustment_interval = if new_difficulty_protocol {
        1
    } else {
        config.difficulty_adjustment_interval
    };
```

**File:** contract/src/dogecoin.rs (L286-297)
```rust
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

**File:** contract/src/bitcoin.rs (L81-86)
```rust
    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
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

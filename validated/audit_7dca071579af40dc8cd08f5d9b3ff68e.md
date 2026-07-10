### Title
Fork Difficulty Computed Against Mainchain Ancestor Instead of Fork Ancestor, Enabling Reduced-Work Fork Acceptance — (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`)

---

### Summary

In all three chain modules, when computing the required difficulty for a fork block, the contract fetches the reference block using `get_header_by_height`, which unconditionally returns the **mainchain** block at that height. When the fork has diverged from the mainchain before the reference height, the fork's actual ancestor at that height is a different block with a different timestamp. The difficulty calculation therefore uses the wrong timestamp, producing an incorrect `expected_bits` value. This cross-module desynchronization between fork state and mainchain state allows an attacker to submit fork blocks with lower difficulty than the protocol requires, reducing the computational cost of a fork attack. The bug is explicitly flagged with a developer TODO in `dogecoin.rs`.

---

### Finding Description

In `get_next_work_required` (Dogecoin, Bitcoin, Litecoin), the difficulty for a new block is computed by comparing the timestamp of the previous block with the timestamp of a reference block at a specific height. The reference block is fetched via:

**`contract/src/dogecoin.rs` lines 291–295:** [1](#0-0) 

```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
```

The same pattern appears in Bitcoin and Litecoin:

**`contract/src/bitcoin.rs` line 81:** [2](#0-1) 

**`contract/src/litecoin.rs` line 88:** [3](#0-2) 

The `get_header_by_height` implementation only looks up `mainchain_height_to_header`, which is exclusively populated with mainchain blocks:

**`contract/src/lib.rs` lines 677–682:** [4](#0-3) 

Fork blocks are stored only in `headers_pool`, never in `mainchain_height_to_header`. Therefore, `get_header_by_height` **always returns the mainchain block** at the requested height, even when the fork has diverged before that height.

**Dogecoin concrete trigger (1-block adjustment interval, active after height 145,000):**

For a fork block at height H, the reference height is `H-2`: [5](#0-4) 

If the fork diverged from the mainchain at height D ≤ H-2, then:
- `prev_block_header` = fork block at H-1 (correct — the actual fork ancestor)
- `first_block_time` = mainchain block at H-2 (incorrect — should be fork block at H-2)

The difficulty is then computed as: [6](#0-5) 

```rust
let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;
```

`first_block_time` is the mainchain block's timestamp, not the fork's ancestor's timestamp. These differ whenever the fork diverged at or before height H-2.

The resulting `expected_bits` is then compared against the submitted block's `bits`: [7](#0-6) 

If the incorrect `modulated_timespan` is larger than the correct one (mainchain reference block has an earlier timestamp than the fork's ancestor at that height), the computed target is larger (lower difficulty), and the attacker's block with lower `bits` passes the check.

The PoW check then uses the attacker-supplied lower `bits` as the target: [8](#0-7) 

---

### Impact Explanation

The corrupted invariant is `expected_bits` in the difficulty check. When the wrong reference block is used:

1. The contract accepts a fork block whose `bits` field encodes a lower difficulty than the protocol requires.
2. The PoW check uses the attacker-supplied lower target, so the parent block's hash only needs to satisfy the reduced target.
3. The attacker mines fork blocks with less computational work than required.
4. Each accepted fork block accumulates `chain_work = work_from_bits(block_header.bits)`. With lower bits, each block contributes less work, but the attacker can mine many such blocks cheaply.
5. If the attacker accumulates enough chain_work to exceed the mainchain tip, `submit_block_header_inner` triggers `reorg_chain`, corrupting the canonical chain and invalidating all SPV proofs anchored to the displaced mainchain blocks.

The canonical chain state (`mainchain_tip_blockhash`, `mainchain_height_to_header`, `mainchain_header_to_height`) is the corrupted output. Any downstream consumer calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against a reorged-in block would receive incorrect inclusion results.

---

### Likelihood Explanation

For **Dogecoin** (1-block adjustment interval after height 145,000), the attack requires only a 2-block divergence depth. The attacker:

1. Observes the mainchain state — specifically, the timestamp of the mainchain block at height H-2.
2. Submits a fork diverging at height D = H-2 (or earlier).
3. Crafts the fork block at H-1 with a late timestamp (within the 2-hour future-time window), maximizing `modulated_timespan` relative to the early mainchain reference block.
4. Submits the fork block at H with the resulting lower `expected_bits`.

This is a realistic, low-barrier attack: the attacker is an unprivileged NEAR caller invoking `submit_blocks`, no privileged role is required, and the mainchain timestamps needed to compute the advantage are publicly observable on-chain.

For **Bitcoin** and **Litecoin** (2016-block adjustment interval), the fork must diverge more than 2016 blocks before the retarget boundary, making exploitation significantly harder in practice.

---

### Recommendation

Replace `get_header_by_height` with an ancestor-walking function that traverses the fork's own `prev_block_hash` chain to find the block at `height_first`. This ensures the reference timestamp always comes from the fork's actual ancestor, not the mainchain block at the same height. The existing `get_prev_header` traversal pattern (used in `reorg_chain` and the min-difficulty testnet walk) demonstrates the correct approach.

---

### Proof of Concept

**Setup (Dogecoin mainnet, height > 145,000):**

1. Mainchain has blocks at heights …, H-3, H-2, H-1, H with timestamps T_{H-3}, **T_{H-2}** (early), T_{H-1}, T_H.
2. Attacker submits a fork diverging at height H-3:
   - Fork block at H-2: timestamp T'_{H-2} (late, e.g., T_{H-2} + 3600s, within the 2-hour window).
   - Fork block at H-1: timestamp T'_{H-1} (late, e.g., T'_{H-2} + 60s).
3. Contract computes difficulty for fork block at H:
   - `prev_block_header.time = T'_{H-1}` (fork block at H-1, correct)
   - `first_block_time = T_{H-2}` ← **mainchain block at H-2, not fork block at H-2**
   - `modulated_timespan = T'_{H-1} - T_{H-2}` ≈ 3660s (inflated)
   - Correct value would be `T'_{H-1} - T'_{H-2}` ≈ 60s (the actual fork interval)
   - Inflated timespan → larger target → lower `expected_bits`
4. Attacker submits fork block at H with the lower `expected_bits` and valid PoW against the reduced target.
5. Difficulty check passes: `expected_bits == block_header.bits` ✓ (both use the reduced value).
6. PoW check passes: `pow_hash ≤ target_from_bits(block_header.bits)` ✓ (reduced target).
7. Attacker repeats for subsequent fork blocks, accumulating chain_work cheaply.
8. When fork `chain_work > mainchain chain_work`, `reorg_chain` is triggered, corrupting the canonical chain.

### Citations

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

**File:** contract/src/dogecoin.rs (L149-154)
```rust
        let pow_hash = aux_data.parent_block.block_hash_pow();
        require!(
            self.skip_pow_verification
                || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
            format!("block should have correct pow")
        );
```

**File:** contract/src/dogecoin.rs (L280-297)
```rust
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

**File:** contract/src/dogecoin.rs (L306-318)
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

**File:** contract/src/lib.rs (L677-682)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```

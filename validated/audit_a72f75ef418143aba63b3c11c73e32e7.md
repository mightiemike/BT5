### Title
Difficulty Target Computed from Mainchain Ancestor Instead of Fork Ancestor for Dogecoin Fork Blocks — (`contract/src/dogecoin.rs`)

---

### Summary

In Dogecoin's new difficulty protocol (height ≥ 145,000), `get_next_work_required` fetches the "first block" timestamp via `get_header_by_height`, which unconditionally returns the **mainchain** block at that height. When the block being validated is a fork block whose chain diverged before `height_first`, the function silently uses the wrong block's timestamp, producing an incorrect difficulty target. The developers themselves flagged this with a `TODO` comment at the exact call site.

---

### Finding Description

In `contract/src/dogecoin.rs`, `get_next_work_required` computes the expected difficulty for the next block. For Dogecoin's new DigiShield protocol (height ≥ 145,000), `difficulty_adjustment_interval` is set to `1`, so `blocks_to_go_back` becomes `1` for all non-genesis blocks: [1](#0-0) 

This makes `height_first = prev_block_header.block_height - 1`. The function then fetches the timestamp of the block at that height: [2](#0-1) 

The `TODO` comment at line 291 explicitly acknowledges the problem: *"check if it is correct to get block header by height from mainchain without looping to find the ancestor."*

`get_header_by_height` is implemented in `contract/src/lib.rs` and **only** consults `mainchain_height_to_header`: [3](#0-2) 

When `prev_block_header` is a fork block at height H+1 (fork diverged at height H), `height_first = H`. `get_header_by_height(H)` returns the **mainchain** block at height H, not the fork's ancestor at height H. These are two different blocks with potentially different timestamps. The resulting `modulated_timespan` fed into `calculate_next_work_required` is therefore wrong: [4](#0-3) 

This is the direct analog to the Telepathy bug: just as the Telepathy `LightClient` used `finalizedSlot` (the nested block's slot) instead of `attestedSlot` (the wrapping block's slot) to select the sync committee, this contract uses the **mainchain** block's timestamp instead of the **fork ancestor's** timestamp to select the difficulty target. In both cases, the wrong block's identifying data is used to determine which validation rule applies, and the mismatch surfaces only at a boundary condition (sync committee period edge / fork divergence point).

---

### Impact Explanation

**Case 1 — Valid fork block rejected (liveness):** If the mainchain block at `height_first` has a later timestamp than the fork's ancestor at the same height, `modulated_timespan` is smaller than correct, producing a tighter `expected_bits`. A legitimately mined fork block carrying the correct `bits` for the actual protocol difficulty fails the check `expected_bits == block_header.bits` and is rejected. The light client cannot track the fork, so any SPV proof for a transaction on the reorganized chain cannot be verified until a future block is accepted.

**Case 2 — Invalid fork block accepted (safety):** If the mainchain block at `height_first` has an earlier timestamp, `modulated_timespan` is larger, producing a looser `expected_bits`. A fork block whose PoW hash only satisfies this weaker target (not the true protocol target) passes both the `expected_bits` equality check and the PoW hash check `U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits)`. The contract stores this block and, if its chainwork exceeds the mainchain tip, triggers a reorg to an invalid chain — causing `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` to return results against a fraudulent chain state. [5](#0-4) 

---

### Likelihood Explanation

**High** for the liveness case, **medium** for the safety case.

- The new difficulty protocol activates at height 145,000, which is already in the past for Dogecoin mainnet. Every Dogecoin fork block that is **two or more blocks** past the divergence point triggers the wrong-ancestor lookup. Chain reorganizations of 2+ blocks are routine.
- The entry path is `submit_blocks`, callable by any trusted relayer with no additional privilege. The relayer is an off-chain daemon that submits adversarially-shaped headers; a malicious relayer can craft fork headers with timestamps chosen to maximize the timestamp delta between the mainchain block and the fork ancestor.
- The safety case requires the attacker to control a relayer and to know the mainchain timestamps at the relevant heights (both are public on-chain data).

---

### Recommendation

Replace the `get_header_by_height` call in `get_next_work_required` with a traversal that walks backwards from `prev_block_header` using `get_prev_header` until reaching `height_first`. This ensures the timestamp comes from the actual ancestor on the chain being validated, not from the mainchain at the same height. The same fix should be applied to the analogous call in `contract/src/bitcoin.rs`: [6](#0-5) 

---

### Proof of Concept

1. Dogecoin mainchain has blocks at heights 145,000 (H) and 145,001 (H+1). Mainchain block at H has timestamp `T_main = 1700000000`.
2. A fork diverges at height H. The fork block at height H has timestamp `T_fork = 1700000060` (60 seconds later than the mainchain block at the same height).
3. The fork continues: fork block at H+1 has timestamp `T_fork_H1 = 1700000120`.
4. A relayer calls `submit_blocks` with the fork block at height H+2.
5. `get_next_work_required` is called with `prev_block_header` = fork block at H+1.
6. `height_first = (H+1) - 1 = H`.
7. `get_header_by_height(H)` returns the **mainchain** block at H with `T_main = 1700000000`.
8. `modulated_timespan = T_fork_H1 - T_main = 1700000120 - 1700000000 = 120` seconds.
9. Correct value: `T_fork_H1 - T_fork = 1700000120 - 1700000060 = 60` seconds.
10. The doubled `modulated_timespan` produces a looser `expected_bits` than the protocol requires. A fork block at H+2 whose PoW hash only satisfies the weaker target is accepted by the contract, while a legitimately mined fork block carrying the correct `bits` is rejected. [7](#0-6)

### Citations

**File:** contract/src/dogecoin.rs (L149-154)
```rust
        let pow_hash = aux_data.parent_block.block_hash_pow();
        require!(
            self.skip_pow_verification
                || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
            format!("block should have correct pow")
        );
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

**File:** contract/src/dogecoin.rs (L307-309)
```rust
    let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;

    let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;
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

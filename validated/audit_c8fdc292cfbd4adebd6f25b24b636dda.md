### Title
Fork Difficulty Retarget Uses Mainchain Ancestor Instead of Fork Ancestor, Enabling Difficulty Manipulation — (`contract/src/bitcoin.rs`, `contract/src/litecoin.rs`, `contract/src/dogecoin.rs`)

---

### Summary

When validating a fork block that falls on a difficulty retarget boundary, the contract computes the expected difficulty using a block fetched from the **mainchain height index** rather than from the fork's own ancestor chain. Because the fork may have diverged before the retarget interval's start block, the mainchain block at that height can have a different timestamp than the fork's actual ancestor. This desynchronization between the two contexts — fork validation vs. mainchain state — allows an unprivileged relayer to submit fork blocks whose difficulty target is computed from the wrong ancestor, bypassing the correct difficulty enforcement.

---

### Finding Description

The `get_next_work_required` function in all three chain modules (Bitcoin, Litecoin, Dogecoin) computes the expected difficulty for a new block at a retarget boundary by fetching the first block of the retarget interval via `blocks_getter.get_header_by_height(first_block_height)`. [1](#0-0) 

The `get_header_by_height` implementation resolves this lookup exclusively from `mainchain_height_to_header`: [2](#0-1) 

This is correct when the block being validated extends the mainchain tip. However, when a **fork block** is being validated (the `else` branch in `submit_block_header_inner`), the fork may have diverged from the mainchain at a height *before* the retarget interval's start block (`first_block_height`). In that case, the fork's actual ancestor at `first_block_height` is a different block — with a potentially different timestamp — than the mainchain block at that same height. [3](#0-2) 

The Dogecoin module even contains a developer TODO acknowledging this exact problem: [4](#0-3) 

The same structural flaw exists identically in the Litecoin module: [5](#0-4) 

---

### Impact Explanation

The difficulty target (`bits`) for a fork block at a retarget boundary is computed from the wrong ancestor's timestamp. The contract then enforces `expected_bits == block_header.bits`: [6](#0-5) 

If the mainchain's retarget-interval start block has a timestamp that produces a **lower difficulty** (higher target value) than the fork's actual ancestor would produce, the attacker can submit fork blocks with that lower difficulty. Lower difficulty means less proof-of-work required per block. With enough such blocks, the fork's accumulated `chain_work` can exceed the mainchain's, triggering a chain reorganization: [7](#0-6) 

This corrupts the canonical chain tracked by the light client, causing `verify_transaction_inclusion` to accept proofs against a fraudulent chain tip and reject proofs against the legitimate chain.

---

### Likelihood Explanation

The attack requires:
1. The fork diverges before the retarget interval start block (2016 blocks back for Bitcoin/Litecoin; 1 block back for post-145k Dogecoin).
2. The mainchain's retarget-interval start block has a timestamp that yields a lower difficulty than the fork's actual ancestor.

For Dogecoin (post-block 145,000), the retarget interval is 1 block, meaning every fork block at any height is a retarget block. The `first_block_height` is `prev_block_header.block_height - 1` (or `0`), and the fork's ancestor at that height is almost always different from the mainchain block at that height once the fork diverges. This makes the Dogecoin variant the most easily triggered.

For Bitcoin and Litecoin, the attacker must build a fork spanning at least 2016 blocks, which requires significant mining resources but is not impossible for a motivated adversary targeting a light client.

---

### Recommendation

When computing the retarget difficulty for a fork block, the contract must walk the fork's own ancestor chain to find the block at `first_block_height`, rather than using `get_header_by_height` which reads from the mainchain index. Concretely, `get_next_work_required` should traverse `get_prev_header` links starting from `prev_block_header` until reaching the block at `first_block_height`, instead of calling `blocks_getter.get_header_by_height(first_block_height)`.

This requires that all fork ancestors back to the retarget interval start are stored in `headers_pool` before the retarget block is submitted — which is already a prerequisite for the fork to be valid (each block's `prev_block_hash` must resolve in the pool).

---

### Proof of Concept

**Setup (Bitcoin/Litecoin):**

1. Mainchain has blocks at heights 0–4031. The retarget boundary is at height 2016. The mainchain block at height 0 (start of first interval) has timestamp `T_main`.
2. An attacker builds a fork starting from height 0 (or any height ≤ 0 in the first interval). The fork's block at height 0 has timestamp `T_fork`, where `T_fork` is much earlier than `T_main`, making the computed `actual_time_taken` larger, which produces a **lower difficulty** (higher target).
3. The attacker submits fork blocks for heights 1–2016. When the contract validates the fork block at height 2016 (retarget boundary), it calls `get_header_by_height(0)`, which returns the **mainchain** block at height 0 with timestamp `T_main`.
4. The expected bits are computed from `T_main`, yielding a lower difficulty than the fork's actual ancestor (`T_fork`) would require.
5. The attacker submits the fork block at height 2016 with `bits` matching the mainchain-derived (lower) difficulty. The contract accepts it.
6. The attacker continues mining fork blocks at this reduced difficulty. Eventually `fork.chain_work > mainchain.chain_work`, triggering `reorg_chain`.
7. The light client's canonical chain is now the attacker's fork. All subsequent `verify_transaction_inclusion` calls operate against the fraudulent chain.

**Dogecoin (simpler, post-145k):**

Since `difficulty_adjustment_interval = 1` after block 145,000, every block is a retarget block. `first_block_height = prev_block_header.block_height - 1`. Any fork block whose parent diverges from the mainchain will use the mainchain's block at `prev_height - 1` for the retarget computation instead of the fork's actual ancestor, immediately enabling difficulty manipulation on the very first fork block submitted after the divergence point. [8](#0-7)

### Citations

**File:** contract/src/bitcoin.rs (L21-26)
```rust
        let expected_bits = get_next_work_required(&config, block_header, prev_block_header, self);

        require!(
            expected_bits == block_header.bits,
            "bad-diffbits: incorrect proof of work"
        );
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

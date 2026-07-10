### Title
Fork Block Difficulty Computed Against Mainchain Ancestor Instead of Fork Ancestor — (`contract/src/dogecoin.rs`, `contract/src/litecoin.rs`, `contract/src/bitcoin.rs`)

---

### Summary

`get_next_work_required` in all three chain modules retrieves the retarget boundary block via `blocks_getter.get_header_by_height(height_first)`, which is implemented to look up `mainchain_height_to_header` — the mainchain index — rather than traversing the fork's own ancestry. When a fork block is being validated, the contract uses the mainchain block's timestamp as the difficulty reference instead of the fork's actual ancestor at that height. This is the same class of bug as M-10: a stored historical value (the mainchain block at a given height) is used in place of the correct current value (the fork's actual ancestor at that height), producing an incorrect difficulty target.

The code itself acknowledges the uncertainty with an explicit TODO comment in `dogecoin.rs`.

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

This always returns the **mainchain** block at the requested height. Fork blocks are stored only in `headers_pool` via `store_fork_header` and are never inserted into `mainchain_height_to_header`. [2](#0-1) 

In `dogecoin.rs`, `get_next_work_required` computes `height_first` and then calls:

```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [3](#0-2) 

The same pattern appears in `litecoin.rs`:

```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
``` [4](#0-3) 

And in `bitcoin.rs`:

```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
``` [5](#0-4) 

When a fork block at height H is being validated and the fork diverged at or before `height_first`, the block at `height_first` on the fork chain is a **different block** from the mainchain block at that height. The contract uses the mainchain block's timestamp, producing a different `first_block_time` than the correct fork ancestor's timestamp. This feeds into `calculate_next_work_required`, yielding an incorrect `expected_bits`.

The Dogecoin case is the most severe: with `difficulty_adjustment_interval = 1` (active for all mainnet blocks at height ≥ 145,000), `blocks_to_go_back` is set to `1` for every non-genesis block, so `height_first = prev_block_header.block_height - 1`. [6](#0-5) 

This means **every Dogecoin fork block** submitted for a fork that diverged two or more blocks ago is validated against the wrong difficulty target. [7](#0-6) 

---

### Impact Explanation

**Incorrect difficulty enforcement for fork blocks.** Two directions of impact:

1. **Under-enforcement (primary security impact):** If the mainchain block at `height_first` has a more recent timestamp than the fork's actual ancestor at that height, `modulated_timespan` is computed as larger, yielding a lower difficulty (higher target). The contract then accepts a fork block whose `bits` field encodes this lower difficulty, even though the fork's own chain history would require a higher difficulty. The block passes `expected_bits == block_header.bits` and is stored in `headers_pool`. An attacker who engineers this scenario can submit fork blocks with less actual PoW than the fork's consensus rules require. [8](#0-7) 

2. **Over-enforcement (secondary impact):** If the mainchain block at `height_first` has an older timestamp, the computed difficulty is higher than required, causing valid fork blocks to be rejected. This is a DoS on fork submission and is noted only for completeness.

The primary impact corrupts the `headers_pool` with fork blocks that do not satisfy the correct PoW requirement for their chain. If enough such blocks accumulate, the fork's `chain_work` (computed from the claimed `bits`) could be used to trigger a chain reorganization via `reorg_chain`, corrupting `mainchain_height_to_header`, `mainchain_header_to_height`, and `mainchain_tip_blockhash`. Downstream callers of `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` would then operate against a corrupted mainchain. [9](#0-8) 

---

### Likelihood Explanation

For Dogecoin mainnet (height ≥ 145,000), the retarget fires on every block, so the bug is triggered for every fork block in any fork that is at least two blocks deep. An unprivileged NEAR caller acting as a relayer can submit adversarial fork chains via `submit_blocks`. No privileged role is required. The attacker controls the fork chain's block timestamps (within the MTP constraint) and can engineer the timestamp differential needed to shift the difficulty target in the desired direction. [10](#0-9) 

For Bitcoin and Litecoin, the retarget fires every 2016 blocks, so the bug is triggered only when a fork spans a retarget boundary — less frequent but still reachable by a persistent attacker.

---

### Recommendation

Replace the `get_header_by_height` call with an ancestor traversal that walks the fork's own `prev_block_hash` chain backward from `prev_block_header` to reach the block at `height_first`. This mirrors the correct behavior of the reference implementations, which traverse the chain lineage rather than looking up a height index.

```rust
// Instead of:
let first_block_time = blocks_getter.get_header_by_height(height_first).block_header.time;

// Use ancestor traversal:
let mut cursor = prev_block_header.clone();
while cursor.block_height > height_first {
    cursor = blocks_getter.get_prev_header(&cursor.block_header);
}
let first_block_time = cursor.block_header.time;
```

This fix must be applied consistently in `dogecoin.rs`, `litecoin.rs`, and `bitcoin.rs`.

---

### Proof of Concept

**Setup (Dogecoin mainnet, height > 145,000):**

1. The mainchain has blocks at heights H-2 (mainchain), H-1 (mainchain), H (mainchain tip). The mainchain block at H-2 has `time = T_main`.

2. An attacker constructs a fork diverging at height H-2. The fork block at H-2 has `time = T_fork` where `T_fork < T_main` (older timestamp, within MTP rules).

3. The attacker submits the fork block at H-1 (fork). The contract validates it using `height_first = H-3`, which is shared ancestry — no impact yet.

4. The attacker submits the fork block at H (fork), with `prev = fork block at H-1`. The contract computes:
   - `height_first = H - 2`
   - `blocks_getter.get_header_by_height(H-2)` → returns **mainchain block at H-2** with `time = T_main`
   - `modulated_timespan` is computed using `T_main` (more recent) → lower difficulty
   - `expected_bits` encodes this lower difficulty

5. The attacker mines the fork block at H with the lower difficulty. The check `expected_bits == block_header.bits` passes. The block is accepted into `headers_pool`.

6. The correct computation would have used `T_fork` (older) → higher difficulty → the attacker's block would have been rejected. [11](#0-10) [1](#0-0)

### Citations

**File:** contract/src/lib.rs (L166-198)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
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

        self.run_mainchain_gc(num_of_headers);
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );

        let refund = amount.saturating_sub(required_deposit);
        if refund > NearToken::from_near(0) {
            Promise::new(env::predecessor_account_id())
                .transfer(refund)
                .into()
        } else {
            PromiseOrValue::Value(())
        }
    }
```

**File:** contract/src/lib.rs (L562-567)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
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

**File:** contract/src/dogecoin.rs (L300-332)
```rust
// source https://github.com/dogecoin/dogecoin/blob/2c513d0172e8bc86fe9a337693b26f2fdf68a013/src/dogecoin.cpp#L41
fn calculate_next_work_required(
    config: &DogecoinConfig,
    prev_block_header: &ExtendedHeader,
    first_block_time: i64,
) -> u32 {
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

**File:** contract/src/litecoin.rs (L88-93)
```rust
    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
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

**File:** btc-types/src/network.rs (L82-93)
```rust
        Network::Mainnet => DogecoinConfig {
            difficulty_adjustment_interval: 1,
            pow_target_timespan: 60,
            proof_of_work_limit_bits: 0x1e0fffff,
            pow_target_spacing: 60, // 1 minute
            pow_allow_min_difficulty_blocks: false,
            pow_limit: U256::new(
                0x0000_0fff_ffff_ffff_ffff_ffff_ffff_ffff,
                0xffff_ffff_ffff_ffff_ffff_ffff_ffff_ffff,
            ),
            aux_chain_id: 0x0062,
        },
```

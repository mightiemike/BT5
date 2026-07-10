### Title
Dogecoin DigiShield Retarget Uses Main-Chain Ancestor Instead of Fork Ancestor, Enabling Difficulty Manipulation on Fork Blocks — (File: `contract/src/dogecoin.rs`)

---

### Summary

The Dogecoin difficulty retarget function `get_next_work_required` fetches the "first block" timestamp for the DigiShield window using `get_header_by_height`, which always reads from the canonical main-chain index (`mainchain_height_to_header`). When a fork block is being validated, this returns the main-chain block at that height rather than the fork's actual ancestor. The developer acknowledged this uncertainty with a TODO comment that was never resolved. An unprivileged NEAR caller submitting adversarial fork blocks via `submit_blocks()` can exploit the mismatch to obtain an incorrectly computed (easier) difficulty target, allowing fork blocks to pass `check_pow` with less PoW than the Dogecoin protocol actually requires.

---

### Finding Description

In `contract/src/dogecoin.rs`, `get_next_work_required` computes the DigiShield retarget window boundary: [1](#0-0) 

The call at line 292–295 delegates to `BlocksGetter::get_header_by_height`, whose only implementation is in `lib.rs`: [2](#0-1) 

This implementation reads exclusively from `mainchain_height_to_header` — the canonical-chain height index. It has no knowledge of which fork the block being validated belongs to. When `submit_block_header` is called for a fork block, `prev_block_header` is the fork's tip (fetched via `get_prev_header` using the hash link), but `get_header_by_height(height_first)` silently returns the **main-chain** block at that height, not the fork's ancestor at that height.

After height 145,000, DigiShield sets `difficulty_adjustment_interval = 1`, so every block triggers a retarget and `height_first = prev_block_height - 1`. [3](#0-2) 

The developer explicitly flagged this as unresolved: [4](#0-3) 

---

### Impact Explanation

`calculate_next_work_required` computes:

```
actual_timespan  = prev_block.time − first_block_time
modulated        = retarget + (actual_timespan − retarget) / 8   [DigiShield damping]
new_target       = prev_target × modulated / retarget
``` [5](#0-4) 

If the main-chain block at `height_first` has an **earlier** timestamp than the fork's actual ancestor at that height, `actual_timespan` is inflated, `modulated_timespan` is pushed toward `max_timespan`, and `new_target` (the difficulty target) is made **easier** than the fork's chain would legitimately require.

The incorrectly computed `expected_bits` is then used in `check_pow`: [6](#0-5) 

A fork block carrying this easier `bits` value passes the target check. Its `chain_work` is accumulated using `work_from_bits(block_header.bits)`, and if the fork's total chain work exceeds the main chain's, `reorg_chain` is triggered, corrupting the contract's canonical chain mapping. [7](#0-6) 

The corrupted canonical chain directly affects `verify_transaction_inclusion` and `verify_transaction_inclusion_v2`, which rely on `mainchain_header_to_height` to confirm that a block belongs to the main chain.

---

### Likelihood Explanation

The attack is reachable by any unprivileged NEAR account via the public `submit_blocks()` entry point. The only prerequisite is that the contract has processed a real Dogecoin fork (which is a normal operational event) and that the main-chain block at `height_first` has an earlier timestamp than the fork's ancestor at that height — a condition that is common in practice because real Dogecoin blocks are mined at irregular intervals. The attacker must mine blocks at the (incorrectly computed) easier difficulty, which is a realistic capability for a motivated adversary targeting a deployed light client.

---

### Recommendation

Replace the `get_header_by_height` call with an ancestor walk that follows the fork's hash-linked chain back to `height_first`. The correct approach is to start from `prev_block_header` and call `get_prev_header` repeatedly until the target height is reached, mirroring how `get_median_time_past` in `utils.rs` correctly traverses the chain by hash links rather than by height index. [8](#0-7) 

---

### Proof of Concept

1. Deploy the Dogecoin light client contract on NEAR testnet with a genesis at height ≥ 145,001 (DigiShield active).
2. Establish a main chain where the block at height `H−1` has timestamp `T_main` (e.g., a real Dogecoin block with a low timestamp relative to its successor).
3. Submit a fork that diverges before height `H−1`. The fork's block at height `H−1` has timestamp `T_fork > T_main` (e.g., set to just above MTP, which is valid per `check_pow`'s timestamp checks).
4. When submitting the fork's block at height `H`, `get_next_work_required` calls `get_header_by_height(H−1)` and receives the **main-chain** block with `T_main`, not the fork's block with `T_fork`.
5. Because `T_main < T_fork`, `actual_timespan` is larger, `modulated_timespan` is larger, and `new_target` is easier than the fork's chain legitimately requires.
6. Mine and submit a fork block at height `H` with `bits` matching this easier target. `check_pow` accepts it because `expected_bits == block_header.bits`.
7. Repeat for subsequent fork blocks. Once the fork's accumulated `chain_work` exceeds the main chain's, `reorg_chain` fires and the contract's canonical chain is replaced with the attacker's fork — a chain that was accepted at a lower difficulty than the Dogecoin protocol mandates.

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

**File:** contract/src/dogecoin.rs (L244-249)
```rust
    let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
    let difficulty_adjustment_interval = if new_difficulty_protocol {
        1
    } else {
        config.difficulty_adjustment_interval
    };
```

**File:** contract/src/dogecoin.rs (L286-295)
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
```

**File:** contract/src/dogecoin.rs (L307-330)
```rust
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

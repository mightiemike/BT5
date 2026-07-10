### Title
Difficulty Retarget Reads Main-Chain Block Instead of Fork Ancestor, Enabling Incorrect PoW Validation for Fork Submissions — (File: `contract/src/dogecoin.rs`)

---

### Summary

`get_next_work_required` in `dogecoin.rs` (and analogously in `litecoin.rs` and `bitcoin.rs`) fetches the difficulty-interval boundary block by height from `mainchain_height_to_header` — the current canonical chain index — rather than walking the fork's actual ancestor chain. When a fork block is being validated, the function silently substitutes the main-chain block at the required height for the fork's true ancestor at that height. This is the direct analog of the reported `LoadVersionAndUpgrade` bug: a height argument is passed, but the wrong state (latest/main-chain state instead of the fork-specific historical state) is loaded and used for the computation.

---

### Finding Description

`get_header_by_height` is implemented as:

```rust
// contract/src/lib.rs  lines 677-682
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [1](#0-0) 

It unconditionally reads from `mainchain_height_to_header`, the canonical-chain index. This is called during fork-block PoW validation inside `get_next_work_required` in `dogecoin.rs`:

```rust
// contract/src/dogecoin.rs  lines 291-295
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [2](#0-1) 

The developer's own `TODO` comment acknowledges the concern. The same pattern appears in `litecoin.rs` and `bitcoin.rs`: [3](#0-2) [4](#0-3) 

When a fork block is submitted, `prev_block_header` is the fork's parent (correctly fetched from `headers_pool` which includes fork blocks). The difficulty boundary height `height_first` is computed relative to that fork parent. But `get_header_by_height(height_first)` returns the **main-chain** block at that height, not the fork's actual ancestor at that height. If the fork diverged at or before `height_first`, these are two different blocks with different timestamps, producing a different `first_block_time` and therefore a different expected difficulty.

For Dogecoin post-block 145,000, the per-block Digishield algorithm sets `difficulty_adjustment_interval = 1`, so `height_first = prev_block_header.block_height - 1`. This means **every single fork block** submitted after height 145,000 has its difficulty computed against the main-chain block one height below the fork parent, not the fork's own ancestor at that height. [5](#0-4) 

---

### Impact Explanation

An attacker who mines a Dogecoin fork can craft the fork's ancestor timestamps to exploit the discrepancy:

1. The fork's ancestor at `height_first` has a very recent timestamp (short actual timespan → Digishield would normally raise difficulty).
2. The main-chain block at `height_first` has an older timestamp (longer apparent timespan → Digishield computes a lower difficulty).
3. The contract accepts the fork block with the lower (attacker-favorable) difficulty because `expected_bits` is derived from the wrong block.

Accepted fork blocks with artificially low `bits` accumulate less `chain_work` per block, but the attacker can mine them proportionally more cheaply. If the attacker submits enough such blocks, `current_header.chain_work > total_main_chain_chainwork` triggers `reorg_chain`, corrupting the canonical chain pointer (`mainchain_tip_blockhash`) and the `mainchain_height_to_header` / `mainchain_header_to_height` indexes. Downstream callers of `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` then operate against a fraudulent canonical chain, causing SPV proofs for non-existent or reorged-out transactions to return `true`. [6](#0-5) 

---

### Likelihood Explanation

- **Entry point is fully unprivileged**: `submit_blocks` is callable by any NEAR account (subject only to the `trusted_relayer` gate, which can be bypassed if the relayer role is open or the attacker is a registered relayer).
- **Dogecoin per-block retarget makes this trigger on every fork block** after height 145,000, not just at 2016-block boundaries.
- **The TODO comment confirms the developers identified this as an open question**, meaning no compensating control was deliberately added.
- Mining a short fork with manipulated timestamps is within reach of any party with modest Dogecoin hash rate.

---

### Recommendation

Replace the height-based main-chain lookup with an ancestor walk along the fork's own chain. Instead of:

```rust
let first_block_time = blocks_getter.get_header_by_height(height_first).block_header.time;
```

Walk backwards from `prev_block_header` by `blocks_to_go_back` steps using `get_prev_header`, which correctly follows the fork's parent links through `headers_pool` regardless of main-chain membership. This is the approach already used by `get_median_time_past` and by `zcash_get_next_work_required`, both of which traverse via `get_prev_header` rather than `get_header_by_height`. [7](#0-6) [8](#0-7) 

---

### Proof of Concept

1. Deploy the Dogecoin-feature contract on NEAR testnet.
2. Submit a legitimate main chain up to height H (post-145,000). Record the main-chain block at height H−1 with timestamp `T_main`.
3. Construct a fork starting at height H−1 whose ancestor at H−1 has timestamp `T_fork` where `T_fork > T_main + large_delta` (making the fork's true Digishield difficulty higher than the main chain's).
4. Call `submit_blocks` with a fork block at height H whose `bits` field encodes the difficulty computed from `T_main` (the main-chain timestamp), not `T_fork`.
5. Observe that `check_pow` passes: `expected_bits` is derived from `get_header_by_height(H-1)` which returns the main-chain block (timestamp `T_main`), not the fork ancestor (timestamp `T_fork`).
6. The fork block is stored with chain_work computed from the attacker-chosen `bits`. Repeat until fork chain_work exceeds main chain, triggering `reorg_chain` and corrupting the canonical chain state. [9](#0-8) [10](#0-9)

### Citations

**File:** contract/src/lib.rs (L531-568)
```rust
    fn submit_block_header_inner(
        &mut self,
        current_header: ExtendedHeader,
        prev_block_header: &ExtendedHeader,
    ) {
        // Main chain submission
        if prev_block_header.block_hash == self.mainchain_tip_blockhash {
            // Probably we should check if it is not in a mainchain?
            // chainwork > highScore
            log!("Block {}: saving to mainchain", current_header.block_hash);
            // Validate chain
            assert_eq!(
                self.mainchain_tip_blockhash,
                current_header.block_header.prev_block_hash
            );

            self.store_block_header(&current_header);
            self.mainchain_tip_blockhash = current_header.block_hash;
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

**File:** contract/src/dogecoin.rs (L166-204)
```rust
    pub(crate) fn submit_block_header(
        &mut self,
        header: (Header, Option<AuxData>),
        skip_pow_verification: bool,
    ) {
        let (block_header, aux_data) = header;

        let prev_block_header = self.get_prev_header(&block_header);
        let current_block_hash = block_header.block_hash();

        if !skip_pow_verification {
            self.check_target(&block_header, &prev_block_header);

            if let Some(ref aux_data) = aux_data {
                self.check_aux(&block_header, aux_data);
            } else {
                let pow_hash = block_header.block_hash_pow();
                // Check if the block hash is less than or equal to the target
                require!(
                    U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
                    format!("block should have correct pow")
                );
            }
        }

        let (current_block_computed_chain_work, overflow) = prev_block_header
            .chain_work
            .overflowing_add(work_from_bits(block_header.bits));
        require!(!overflow, "Addition of U256 values overflowed");

        let current_header = ExtendedHeader {
            block_header: block_header.clone().into_light(),
            block_hash: current_block_hash,
            chain_work: current_block_computed_chain_work,
            block_height: 1 + prev_block_header.block_height,
        };

        self.submit_block_header_inner(current_header, &prev_block_header);
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

**File:** contract/src/utils.rs (L10-25)
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

### Title
Dogecoin Difficulty Calculation Uses Mainchain Timestamp Instead of Fork Ancestor Timestamp — (`contract/src/dogecoin.rs`)

---

### Summary

`get_next_work_required` in `dogecoin.rs` fetches the "first block" timestamp via `get_header_by_height`, which always reads from `mainchain_height_to_header`. When the function is called during fork-block processing and the fork diverged two or more blocks ago, the mainchain block's timestamp is used instead of the fork's actual ancestor timestamp. This produces an incorrect `actual_timespan`, which in turn produces an incorrect expected difficulty (`expected_bits`). A block that should be rejected for having insufficient PoW can pass `check_pow`, and a valid fork block can be rejected.

---

### Finding Description

`get_next_work_required` (Dogecoin, post-145 000 era, `difficulty_adjustment_interval = 1`) computes:

```
height_first = prev_block_header.block_height - 1
first_block_time = blocks_getter.get_header_by_height(height_first).block_header.time
``` [1](#0-0) 

`get_header_by_height` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header          // ← always mainchain
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [2](#0-1) 

When a fork block at height H is submitted and the fork diverged at height H-2 or earlier, the fork's ancestor at H-2 (`fork_H-2`) is a different block from the mainchain block at H-2 (`main_H-2`). The contract reads `main_H-2.time` instead of `fork_H-2.time`.

The downstream calculation is:

```rust
let modulated_timespan = retarget_timespan + (actual_timespan - retarget_timespan) / 8;
``` [3](#0-2) 

where `actual_timespan = prev_block_header.time − first_block_time`. A wrong `first_block_time` produces a wrong `modulated_timespan`, a wrong target, and therefore a wrong `expected_bits`. `check_pow` then enforces `expected_bits == block_header.bits`: [4](#0-3) 

The same structural defect exists in `bitcoin.rs` for the 2016-block retarget window, but the fork depth required (≥ 2016 blocks) makes it practically unreachable. The Dogecoin post-145 000 path requires only a 2-block-deep fork.

The code itself acknowledges the uncertainty with a TODO comment:

```
// TODO: check if it is correct to get block header by height from mainchain
// without looping to find the ancestor
``` [5](#0-4) 

Zcash does not share this defect; its difficulty window is traversed via `get_prev_header`, which follows `prev_block_hash` pointers and therefore stays on the fork's own chain. [6](#0-5) 

---

### Impact Explanation

An attacker who controls fork block timestamps can set `fork_H-2.time` to a value later than `main_H-2.time`. Because the contract uses `main_H-2.time` (the earlier value), `actual_timespan` is inflated, `modulated_timespan` is pushed toward its maximum (90 s vs. the correct 45 s floor), and the computed target is up to 2× easier than the protocol requires. The attacker can then submit `fork_H` with `bits` matching this artificially easy target. The block passes `check_pow` even though it would fail under the correct difficulty. Repeated over multiple fork blocks, this allows a fork to accumulate chainwork faster than honest miners, enabling a chain reorganization with less total proof-of-work than the protocol demands. This corrupts the canonical chain mapping stored in `mainchain_height_to_header` / `mainchain_header_to_height` and invalidates SPV proofs issued by `verify_transaction_inclusion`. [7](#0-6) 

---

### Likelihood Explanation

The attack requires only a 2-block-deep fork on the Dogecoin build, which is a normal occurrence during any chain reorganization. The attacker is an unprivileged proof submitter calling `submit_blocks` with adversarial `(Header, Option<AuxData>)` pairs. No privileged role or leaked key is needed. The timestamp manipulation is bounded by the MTP rule and the 2-hour future-time cap, but even a modest delta (tens of seconds) is sufficient to shift the difficulty by the full 2× factor given Dogecoin's 60-second target spacing.

---

### Recommendation

Replace the `get_header_by_height` call with an ancestor traversal that follows `prev_block_hash` pointers from `prev_block_header` backward by `blocks_to_go_back` steps (mirroring the Zcash implementation's use of `get_prev_header`). This ensures the timestamp used for difficulty calculation always belongs to the fork's own ancestry, not the mainchain.

---

### Proof of Concept

1. Mainchain tip is at height H-1. `main_H-2.time = T`.
2. Attacker calls `submit_blocks` with `fork_H-2` (parent = `main_H-3`, `time = T + 600`). This block passes its own difficulty check correctly (its `first_block_time` comes from `main_H-3`, which is shared with the mainchain).
3. Attacker submits `fork_H-1` (parent = `fork_H-2`, `time = T + 601`).
4. Attacker computes the expected bits for `fork_H` as the contract will compute them: `actual_timespan = (T+601) − T = 601 s`; `modulated_timespan` is clamped to `max_timespan = 90 s`; target is scaled up by `90/60 = 1.5×` relative to `fork_H-1.bits`. The correct computation would use `actual_timespan = (T+601) − (T+600) = 1 s`, clamped to `min_timespan = 45 s`, giving a 0.75× (harder) target.
5. Attacker submits `fork_H` with `bits` matching the easier (1.5×) target. `check_pow` computes the same easy target and accepts the block.
6. The block is stored in `headers_pool`. If the fork's cumulative `chain_work` exceeds the mainchain's, `reorg_chain` is triggered, corrupting the canonical chain. [8](#0-7) [9](#0-8)

### Citations

**File:** contract/src/dogecoin.rs (L24-33)
```rust
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

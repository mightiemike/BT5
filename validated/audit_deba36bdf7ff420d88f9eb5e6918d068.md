### Title
Difficulty Adjustment Uses Mainchain Boundary Block Instead of Fork Ancestor, Enabling Manipulated Difficulty on Fork Submissions - (`contract/src/lib.rs`, `contract/src/dogecoin.rs`, `contract/src/litecoin.rs`, `contract/src/bitcoin.rs`)

---

### Summary

When validating a fork block at a difficulty-adjustment boundary, all three chain modules (Bitcoin, Litecoin, Dogecoin) call `get_header_by_height()` to retrieve the first block of the retarget interval. That function unconditionally reads from `mainchain_height_to_header`, returning the **mainchain** block at that height rather than the fork's actual ancestor. An unprivileged proof submitter can exploit the timestamp mismatch between the mainchain boundary block and the fork's true ancestor to cause the contract to compute an artificially low required difficulty for fork blocks, allowing a chain reorganization to be triggered with less cumulative proof-of-work than the protocol requires.

---

### Finding Description

`get_header_by_height` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [1](#0-0) 

It always resolves the height through `mainchain_height_to_header`, which maps only **mainchain** heights to hashes. There is no fork-aware ancestor walk.

All three chain-specific difficulty modules call this function to obtain the first block of the retarget window:

**Bitcoin** (`bitcoin.rs` lines 78–86):
```rust
let first_block_height =
    prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(config, prev_block_header,
    interval_tail_extend_header.block_header.time.into())
``` [2](#0-1) 

**Litecoin** (`litecoin.rs` lines 86–93):
```rust
let first_block_height = prev_block_header.block_height - blocks_to_go_back;
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(config, prev_block_header,
    interval_tail_extend_header.block_header.time.into())
``` [3](#0-2) 

**Dogecoin** (`dogecoin.rs` lines 286–297), where the developers themselves left a `TODO` acknowledging the problem:
```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
``` [4](#0-3) 

The difficulty formula in all three chains computes:

```
actual_time_taken = prev_fork_block_time − mainchain_boundary_block_time
```

instead of the correct:

```
actual_time_taken = prev_fork_block_time − fork_ancestor_boundary_block_time
``` [5](#0-4) 

Because `prev_fork_block_time` is the timestamp of the attacker-controlled fork block (bounded only by MTP and `MAX_FUTURE_BLOCK_TIME_LOCAL`), and `mainchain_boundary_block_time` is a fixed real-chain value, the attacker can choose fork block timestamps to maximize `actual_time_taken`, which is then clamped to `pow_target_timespan × 4`. This produces the minimum possible required difficulty (up to a 4× reduction from the true required difficulty).

The correct approach—used by Bitcoin Core itself—is to walk the ancestor chain of the block being validated to find the boundary block, not to look it up by height in the mainchain index.

---

### Impact Explanation

An attacker who submits a fork diverging before a difficulty-adjustment boundary can cause the contract to accept fork blocks whose `bits` field encodes a target that is up to 4× easier than the protocol-correct target. Because `chain_work` is accumulated from `work_from_bits(header.bits)`, a fork built on artificially easy blocks accumulates less real proof-of-work per block. If the attacker submits enough such blocks to exceed the mainchain's `chain_work`, `reorg_chain` is triggered, replacing the canonical chain with the attacker's fork. Downstream consumers of `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` then operate against a corrupted canonical chain, potentially confirming transactions that were never included in the real Bitcoin/Litecoin/Dogecoin chain. [6](#0-5) 

---

### Likelihood Explanation

The entry point is `submit_blocks`, which is callable by any trusted relayer (an unprivileged NEAR account that has staked). The attack requires:
1. Submitting a fork that diverges at or before a difficulty-adjustment boundary — a normal occurrence during any chain reorganization scenario.
2. Setting fork block timestamps near the maximum allowed future offset to widen the computed `actual_time_taken`.

No privileged role, leaked key, or social engineering is required. The TODO comment in `dogecoin.rs` confirms the developers identified this as an open correctness question, indicating the issue has not been deliberately accepted as safe. [7](#0-6) 

---

### Recommendation

Replace `get_header_by_height` calls inside difficulty-adjustment logic with a proper ancestor walk that follows `prev_block_hash` links through `headers_pool` until the boundary height is reached. This mirrors Bitcoin Core's `GetAncestor` function and ensures the correct fork-specific boundary block timestamp is used regardless of what the mainchain holds at that height.

---

### Proof of Concept

**Setup**: Mainchain has blocks M0…M2016 (a full retarget interval). M0 has `time = T0`. The mainchain's retarget at height 2016 uses `actual_time_taken = M2015.time − M0.time`.

**Attack**:
1. Attacker submits fork block F1 at height 1, diverging from M0. F1's timestamp is set to `T0 + MAX_FUTURE_BLOCK_TIME_LOCAL` (maximum allowed).
2. Attacker builds fork blocks F2…F2016, each with timestamps incrementing minimally (to satisfy MTP).
3. At height 2016, the contract calls `get_header_by_height(0)` and receives **M0** (mainchain block, `time = T0`).
4. `actual_time_taken = F2015.time − M0.time`. Because F2015.time can be up to `T0 + MAX_FUTURE_BLOCK_TIME_LOCAL + 2015×1`, and M0.time = T0, `actual_time_taken` is maximized and clamped to `pow_target_timespan × 4`.
5. The contract computes `expected_bits` corresponding to 4× the target (minimum difficulty). The attacker's fork blocks only need to meet this reduced difficulty.
6. The attacker mines F2017…FN with the reduced difficulty, accumulating `chain_work` faster per unit of real hash power.
7. Once `fork_tip.chain_work > mainchain_tip.chain_work`, `reorg_chain` executes, replacing the canonical chain. [8](#0-7) [9](#0-8)

### Citations

**File:** contract/src/lib.rs (L531-567)
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
```

**File:** contract/src/lib.rs (L575-647)
```rust
    fn reorg_chain(&mut self, fork_tip_header: ExtendedHeader, last_main_chain_block_height: u64) {
        let fork_tip_height = fork_tip_header.block_height;
        if last_main_chain_block_height > fork_tip_height {
            // If we see that main chain is longer than fork we first garbage collect
            // outstanding main chain blocks:
            //
            //      [m1] - [m2] - [m3] - [m4] <- We should remove [m4]
            //     /
            // [m0]
            //     \
            //      [f1] - [f2] - [f3]
            for height in (fork_tip_height + 1)..=last_main_chain_block_height {
                let current_main_chain_blockhash = self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str("cannot get a block"));
                self.remove_block_header(&current_main_chain_blockhash);
                self.mainchain_height_to_header.remove(&height);
            }
        }

        // Now we are in a situation where mainchain is equivalent to fork size:
        //
        //      [m1] - [m2] - [m3] - [m4] <- main tip
        //     /
        // [m0]
        //     \
        //      [f1] - [f2] - [f3] - [f4] <- fork tip
        //
        //
        // Or in a situation where it is shorter:
        //
        //      [m1] - [m2] - [m3] <- main tip
        //     /
        // [m0]
        //     \
        //      [f1] - [f2] - [f3] - [f4] <- fork tip

        let fork_tip_hash = fork_tip_header.block_hash.clone();
        let mut fork_header_cursor = fork_tip_header;

        while !self
            .mainchain_header_to_height
            .contains_key(&fork_header_cursor.block_hash)
        {
            let prev_block_hash = fork_header_cursor.block_header.prev_block_hash;
            let current_block_hash = fork_header_cursor.block_hash;
            let current_height = fork_header_cursor.block_height;

            // Inserting the fork block into the main chain, if some mainchain block is occupying
            // this height let's save its hashcode
            let main_chain_block = self
                .mainchain_height_to_header
                .insert(&current_height, &current_block_hash);
            self.mainchain_header_to_height
                .insert(&current_block_hash, &current_height);

            // If we found a mainchain block at the current height than remove this block from the
            // header pool and from the header -> height map
            if let Some(current_main_chain_blockhash) = main_chain_block {
                self.remove_block_header(&current_main_chain_blockhash);
            }

            // Switch iterator cursor to the previous block in fork
            fork_header_cursor = self
                .headers_pool
                .get(&prev_block_hash)
                .unwrap_or_else(|| env::panic_str("previous fork block should be there"));
        }

        // Updating tip of the new main chain
        self.mainchain_tip_blockhash = fork_tip_hash;
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

**File:** contract/src/bitcoin.rs (L95-103)
```rust
    let prev_block_time: i64 = prev_block_header.block_header.time.into();

    let mut actual_time_taken: i64 = prev_block_time - first_block_time;
    if actual_time_taken < config.pow_target_timespan / 4 {
        actual_time_taken = config.pow_target_timespan / 4;
    }
    if actual_time_taken > config.pow_target_timespan * 4 {
        actual_time_taken = config.pow_target_timespan * 4;
    }
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

**File:** contract/src/dogecoin.rs (L291-297)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;

    calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
```

### Title
Difficulty Retarget Uses Stale Mainchain Ancestor Instead of Fork Ancestor During Fork Submission — (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`)

---

### Summary

When a fork block is submitted at a difficulty-retarget boundary, all three chain implementations (`bitcoin`, `litecoin`, `dogecoin`) compute the expected difficulty using a block fetched from the **mainchain height map** rather than from the fork's actual ancestor chain. If the fork diverges before the retarget look-back height, the wrong block's timestamp is used, producing an incorrect `expected_bits`. This allows an attacker to submit fork blocks whose `bits` field satisfies the incorrectly computed target — potentially with less PoW than the protocol requires — corrupting the fork-choice mechanism.

---

### Finding Description

Every `get_next_work_required` implementation resolves the "first block of the retarget interval" by calling `blocks_getter.get_header_by_height(height_first)`:

**Dogecoin** (`contract/src/dogecoin.rs`, lines 286–297):
```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [1](#0-0) 

**Bitcoin** (`contract/src/bitcoin.rs`, lines 78–86):
```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(
    config,
    prev_block_header,
    interval_tail_extend_header.block_header.time.into(),
)
``` [2](#0-1) 

**Litecoin** (`contract/src/litecoin.rs`, lines 86–93):
```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(
    config,
    prev_block_header,
    interval_tail_extend_header.block_header.time.into(),
)
``` [3](#0-2) 

`get_header_by_height` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [4](#0-3) 

This **always** reads from `mainchain_height_to_header` — the canonical chain map — regardless of whether the block being validated belongs to a fork.

**Concrete trigger (Dogecoin post-height 145,000, `difficulty_adjustment_interval = 1`):**

- Mainchain has blocks at heights `0 … N`. Fork diverges at height `H_fork`.
- Attacker submits fork block at height `H_fork` (1st fork block): `height_first = H_fork − 1`. Since `H_fork − 1 < H_fork`, the mainchain block at that height is the shared ancestor — no bug yet.
- Attacker submits fork block at height `H_fork + 1` (2nd fork block): `height_first = H_fork`. The mainchain block at `H_fork` is **different** from the fork's block at `H_fork`. The contract uses the mainchain block's timestamp to compute `expected_bits`, not the fork's. [5](#0-4) 

For Bitcoin/Litecoin the same desynchronization occurs once the fork is longer than `difficulty_adjustment_interval` blocks and crosses a retarget boundary. [6](#0-5) 

The developers themselves flagged this exact concern with a TODO comment: [7](#0-6) 

---

### Impact Explanation

**Impact: High**

`check_pow` enforces `expected_bits == block_header.bits`: [8](#0-7) 

If `expected_bits` is computed from the wrong (mainchain) ancestor's timestamp, the attacker submits a fork block whose `bits` field matches that incorrect value. The subsequent PoW check then uses `target_from_bits(block_header.bits)` — the attacker-favorable target — to validate the hash: [9](#0-8) 

If the mainchain's block at `height_first` has a timestamp that makes the retarget interval appear longer than the fork's actual interval, the computed difficulty is **lower** (higher target). The attacker can then satisfy PoW with less work per block. A sufficiently long fork built this way can accumulate enough `chain_work` to trigger `reorg_chain`, replacing the canonical chain with the attacker's low-work fork: [10](#0-9) 

Downstream consumers of `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` would then verify proofs against a fraudulent chain. [11](#0-10) 

---

### Likelihood Explanation

**Likelihood: Low**

The attacker must:
1. Control a fork that diverges before a retarget look-back height.
2. Find (or wait for) a mainchain state where the block at `height_first` has a timestamp that produces a lower difficulty than the fork's correct difficulty.
3. Build a fork long enough to exceed the mainchain's `chain_work`.

For Dogecoin (post-145,000, per-block retarget), the window opens on the **2nd fork block**, making it more accessible. For Bitcoin/Litecoin (2016-block interval), the attacker needs a much longer fork. The scenario is realistic for Dogecoin but requires significant resources for Bitcoin/Litecoin.

---

### Recommendation

Replace the mainchain height lookup with an ancestor traversal that walks the fork's own chain. Instead of:

```rust
blocks_getter.get_header_by_height(height_first)
```

traverse backwards from `prev_block_header` by following `prev_block_hash` links until reaching `height_first`. This ensures the timestamp used for difficulty calculation always belongs to the block's actual ancestor, regardless of whether it is on the mainchain or a fork.

---

### Proof of Concept

1. Contract is initialized with a Dogecoin mainchain at heights `0 … 200000` (post-145,000 era, per-block retarget). The mainchain block at height `H` has timestamp `T_main`.

2. Attacker submits a fork block at height `H` (diverging from height `H − 1`). This fork block has timestamp `T_fork` where `T_fork − T_prev_fork` is much smaller than `T_main − T_prev_main` (i.e., the fork's interval is shorter, which would normally mean higher difficulty).

3. Attacker submits a fork block at height `H + 1`. The contract calls `get_next_work_required` with `height_first = H`. It fetches the **mainchain** block at height `H` (timestamp `T_main`), not the fork's block at height `H` (timestamp `T_fork`).

4. Because `T_main − T_prev_main` is larger than `T_fork − T_prev_fork`, the computed `expected_bits` corresponds to **lower difficulty** than the fork's correct difficulty.

5. The attacker submits a fork block at height `H + 1` with `bits = expected_bits` (lower difficulty). The PoW check passes with less work than the fork's correct difficulty would require.

6. Repeated over many blocks, the attacker builds a fork with less total PoW than the mainchain, yet triggers `reorg_chain` if `chain_work` (accumulated from the lower-difficulty `bits`) exceeds the mainchain's `chain_work`. [12](#0-11)

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

**File:** contract/src/dogecoin.rs (L149-154)
```rust
        let pow_hash = aux_data.parent_block.block_hash_pow();
        require!(
            self.skip_pow_verification
                || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
            format!("block should have correct pow")
        );
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

**File:** contract/src/bitcoin.rs (L56-87)
```rust
    if (prev_block_header.block_height + 1) % config.difficulty_adjustment_interval != 0 {
        if config.pow_allow_min_difficulty_blocks {
            if block_header.time
                > prev_block_header.block_header.time + 2 * config.pow_target_spacing
            {
                return config.proof_of_work_limit_bits;
            }

            let mut current_block_header = prev_block_header.clone();
            while current_block_header.block_header.bits == config.proof_of_work_limit_bits
                && current_block_header.block_height % config.difficulty_adjustment_interval != 0
            {
                current_block_header =
                    blocks_getter.get_prev_header(&current_block_header.block_header);
            }

            let last_bits = current_block_header.block_header.bits;
            return last_bits;
        }
        return prev_block_header.block_header.bits;
    }

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

**File:** contract/src/lib.rs (L288-323)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
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

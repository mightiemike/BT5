### Title
Difficulty Calculation Uses Mainchain Block Timestamp Instead of Fork Ancestor's Timestamp, Enabling Canonical Chain Corruption — (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`)

---

### Summary

When validating a fork block's proof-of-work, the difficulty retarget calculation fetches the period-start block from the **mainchain** by height (`get_header_by_height`), not by traversing the fork's actual ancestry. This is the direct analog of the reported bug: a critical security calculation uses an incorrect reference value (mainchain block timestamp ≈ "spot price") instead of the true value (fork ancestor's timestamp ≈ "mark price"). A malicious relayer can exploit the resulting incorrect difficulty target to submit a low-work fork that overtakes the mainchain, corrupting the canonical chain state and causing `verify_transaction_inclusion` to return false positives.

---

### Finding Description

In `get_next_work_required` for Dogecoin, the period-start block is fetched by mainchain height: [1](#0-0) 

The developer explicitly flagged this with a `TODO` comment acknowledging the correctness concern. The same pattern exists in the Bitcoin path: [2](#0-1) 

`get_header_by_height` resolves exclusively from `mainchain_height_to_header`: [3](#0-2) 

When a fork block is being validated, `prev_block_header` is correctly the fork's actual previous block (fetched via `prev_block_hash` from `headers_pool`). However, `height_first` is resolved against the **mainchain**, not the fork's ancestry. If the fork diverged before `height_first`, the mainchain block at that height is a completely different block with a different timestamp than the fork's true ancestor at that height.

For Dogecoin after height 145,000, `difficulty_adjustment_interval = 1`, so this incorrect lookup occurs for **every single fork block** submitted after the second: [4](#0-3) 

The difficulty is then computed as: [5](#0-4) 

A malicious relayer controls the fork block timestamps (bounded only by MTP and `current_timestamp + 7200`). By setting fork block timestamps far ahead of the mainchain's corresponding blocks, the attacker drives `actual_timespan` to its maximum, clamped to `max_timespan = retarget_timespan + retarget_timespan/2 = 90s`. This yields `new_target = prev_target * 90 / 60 = prev_target * 1.5` — a 1.5× difficulty decrease per block. After 20 fork blocks, the difficulty is `0.67^20 ≈ 0.0003×` the original, requiring trivially small PoW per block.

The accepted `bits` value is then used to accumulate chainwork: [6](#0-5) 

And fork promotion is triggered purely by chainwork comparison: [7](#0-6) 

---

### Impact Explanation

Once the fork is promoted, `mainchain_tip_blockhash`, `mainchain_height_to_header`, and `mainchain_header_to_height` all reflect the attacker's fabricated chain. Any downstream NEAR contract calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` will receive results against the attacker's chain: [8](#0-7) 

A transaction confirmed on the real Dogecoin network may return `false` (denial of valid proof), and a transaction on the attacker's fabricated chain may return `true` (acceptance of invalid proof). This breaks the core security guarantee of the light client — SPV proof integrity.

---

### Likelihood Explanation

The `submit_blocks` entry point is gated by `#[trusted_relayer]`: [9](#0-8) 

The `trusted_relayer` macro from `omni_utils` implements a staking mechanism managed by `RelayerManager`/`DAO` roles. In typical deployments of such bridges, staking is permissionless — any NEAR account can stake tokens to become a relayer. If so, the attacker's entry path is fully unprivileged. Even if staking requires approval, a compromised or malicious registered relayer is a realistic threat model for a cross-chain bridge. The developer's own `TODO` comment confirms awareness that this code path may be incorrect.

---

### Recommendation

Replace `get_header_by_height(height_first)` with a function that traverses the fork's actual ancestry by walking `prev_block_hash` links backward from `prev_block_header` until reaching `height_first`. This mirrors how `reorg_chain` already traverses fork ancestry via `prev_block_hash`: [10](#0-9) 

The difficulty retarget must use the timestamp of the block that is the true ancestor of the block being validated at the period-start height, not the mainchain block at that height.

---

### Proof of Concept

1. Honest relayer has submitted mainchain blocks up to height 200,000 on Dogecoin (post-145,000, per-block retarget). Mainchain block at height 199,998 has timestamp `T_main`.

2. Attacker registers as a trusted relayer (via staking) and submits a fork starting at height 199,999, with `prev_block_hash` pointing to the mainchain block at 199,998.

3. For fork block at height 200,001 (k=2), the difficulty calculation fetches the mainchain block at height 199,999 (`height_first = 200,000 - 1`). The attacker's fork block at 200,000 has timestamp `T_main + 7200` (max allowed). The mainchain block at 199,999 has timestamp `T_main + 60` (normal). So `actual_timespan = 7200 - 60 = 7140s`, clamped to `max_timespan = 90s`, yielding `new_target = prev_target * 1.5`.

4. Attacker repeats for each subsequent fork block, each time using the mainchain's block timestamp as the reference, driving difficulty down by 1.5× per block.

5. After 30 fork blocks, difficulty is `≈ 0.00006×` original. Attacker pre-computes 30 fork blocks with trivial PoW off-chain and submits them in one `submit_blocks` call.

6. Total fork chainwork exceeds mainchain chainwork. `reorg_chain` is triggered. `mainchain_tip_blockhash` now points to the attacker's fabricated chain.

7. Downstream contracts calling `verify_transaction_inclusion` against real Dogecoin transactions receive `false`; attacker-fabricated transactions receive `true`.

### Citations

**File:** contract/src/dogecoin.rs (L191-194)
```rust
        let (current_block_computed_chain_work, overflow) = prev_block_header
            .chain_work
            .overflowing_add(work_from_bits(block_header.bits));
        require!(!overflow, "Addition of U256 values overflowed");
```

**File:** contract/src/dogecoin.rs (L244-252)
```rust
    let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
    let difficulty_adjustment_interval = if new_difficulty_protocol {
        1
    } else {
        config.difficulty_adjustment_interval
    };

    if (prev_block_header.block_height + 1) % difficulty_adjustment_interval != 0 {
        if config.pow_allow_min_difficulty_blocks {
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

**File:** contract/src/dogecoin.rs (L307-318)
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
```

**File:** contract/src/bitcoin.rs (L81-87)
```rust
    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
}
```

**File:** contract/src/lib.rs (L166-172)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
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

**File:** contract/src/lib.rs (L563-566)
```rust
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L616-643)
```rust
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
```

**File:** contract/src/lib.rs (L677-683)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
}
```

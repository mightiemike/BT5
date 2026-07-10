### Title
Fork Difficulty Retarget Uses Mainchain Ancestor Timestamp Instead of Fork Ancestor Timestamp — (`contract/src/bitcoin.rs`, `contract/src/dogecoin.rs`)

---

### Summary

When a fork block is submitted at a difficulty-adjustment boundary, both the Bitcoin and Dogecoin `get_next_work_required` implementations look up the first block of the retarget window by **mainchain height** (`get_header_by_height`), not by traversing the fork's actual ancestor chain. If the fork diverged before that window-start height, the mainchain block at that height is not the fork's ancestor, so the timestamp used for difficulty calculation is wrong. This is the direct analog of the "virtual price vs. actual price" desynchronization: the difficulty the contract enforces (computed from the mainchain snapshot) can diverge from the difficulty the fork block should actually satisfy (computed from the fork's own history), allowing fork blocks with insufficient PoW to be accepted.

---

### Finding Description

**Root cause — Bitcoin path (`contract/src/bitcoin.rs`, lines 78–86):**

```rust
let first_block_height =
    prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);

let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(
    config,
    prev_block_header,
    interval_tail_extend_header.block_header.time.into(),
)
``` [1](#0-0) 

**Root cause — Dogecoin path (`contract/src/dogecoin.rs`, lines 291–297):**

```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [2](#0-1) 

The developers themselves flagged this with a `TODO`. `get_header_by_height` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header          // ← always mainchain
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [3](#0-2) 

`mainchain_height_to_header` is a map that is only updated for mainchain blocks. [4](#0-3) 

Fork blocks are stored only in `headers_pool` via `store_fork_header`, which does **not** update `mainchain_height_to_header`. [5](#0-4) 

**The desynchronization:** Suppose the mainchain and a fork share a common ancestor at height `A`, and the retarget window starts at height `W` where `A < W`. The mainchain block at height `W` has timestamp `T_main`. The fork's actual ancestor at height `W` has timestamp `T_fork ≠ T_main`. The contract computes `expected_bits` using `T_main`, but the fork's correct difficulty should be computed using `T_fork`. The two values diverge, and the contract enforces the wrong difficulty on the fork block at the retarget boundary.

---

### Impact Explanation

**If `T_main < T_fork` (mainchain window-start block is earlier):**
- `actual_time = T_prev − T_main` is **larger** than the correct `T_prev − T_fork`.
- The computed target is **easier** (lower bits value = higher target = less work required).
- The contract accepts a fork block whose `bits` field encodes a difficulty lower than the protocol requires.
- That block is stored in `headers_pool`. Its `chain_work` contribution (`work_from_bits(header.bits)`) is computed from the submitted (too-easy) `bits`, so the fork accumulates chainwork at a rate below what honest mining at the correct difficulty would produce.
- If the fork's cumulative chainwork eventually exceeds the mainchain's, `reorg_chain` is triggered, and the canonical chain recorded by the light client contains blocks that never satisfied the correct PoW difficulty.
- Any downstream consumer calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against a block in this reorged chain receives a `true` result for a transaction in a chain that was not honestly mined. [6](#0-5) [7](#0-6) 

---

### Likelihood Explanation

The attacker-controlled entry point is `submit_blocks`, which is gated by `#[trusted_relayer]` but is callable by any registered relayer — an unprivileged NEAR account that has staked. The attack requires:

1. Submitting a fork that diverges from the mainchain before the retarget window-start height `W`.
2. Ensuring the fork's block at height `W` carries a timestamp later than the mainchain's block at `W` (achievable within the MTP and `MAX_FUTURE_BLOCK_TIME_LOCAL` constraints).
3. Mining fork blocks at the easier difficulty from the retarget boundary onward. [8](#0-7) 

The `max_fork_len` config parameter in the relayer limits how far back the relayer will walk, but the contract itself imposes no such limit on fork depth. The TODO comment in the production Dogecoin module confirms the developers are aware the mainchain lookup is potentially incorrect. The same uncorrected pattern exists in the Bitcoin module without any comment.

---

### Recommendation

Replace the height-based mainchain lookup with an ancestor traversal that follows `prev_block_hash` links through `headers_pool` starting from `prev_block_header` until reaching height `first_block_height`. This mirrors what Bitcoin Core does: it walks the actual chain of the block being validated, not a parallel chain at the same heights.

---

### Proof of Concept

**Setup (Bitcoin, `difficulty_adjustment_interval = 2016`):**

1. Mainchain is initialized at height 0 and extended to height 4032. The mainchain block at height 2017 (start of the second retarget window) has timestamp `T_main = 1_700_000_000`.
2. Attacker submits a fork starting at height 2000. The fork's block at height 2017 has timestamp `T_fork = 1_700_100_000` (100,000 seconds later, within the allowed future-time window relative to its MTP).
3. The fork is extended to height 4031 (the block just before the second retarget boundary).
4. Attacker submits the fork block at height 4032. Inside `submit_block_header` → `check_pow` → `get_next_work_required`:
   - `first_block_height = 4031 − 2015 = 2017`
   - `blocks_getter.get_header_by_height(2017)` returns the **mainchain** block with `T_main = 1_700_000_000`.
   - `actual_time = T_4031 − T_main` is 100,000 seconds larger than the correct `T_4031 − T_fork`.
   - `expected_bits` encodes an easier target than the protocol requires.
5. The fork block at height 4032 carries `bits` matching this easier target. The PoW check passes. The block is stored.
6. If the fork's cumulative `chain_work` exceeds the mainchain's, `reorg_chain` executes, and the light client's canonical chain now includes blocks that never satisfied the correct Bitcoin difficulty.
7. A recipient contract calling `verify_transaction_inclusion_v2` with a transaction in one of these fork blocks receives `true`. [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** contract/src/bitcoin.rs (L50-87)
```rust
fn get_next_work_required(
    config: &NetworkConfig,
    block_header: &Header,
    prev_block_header: &ExtendedHeader,
    blocks_getter: &impl BlocksGetter,
) -> u32 {
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

**File:** contract/src/dogecoin.rs (L291-297)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;

    calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
```

**File:** contract/src/lib.rs (L97-99)
```rust
    // A pair of lookup maps that allows to find header by height and height by header
    mainchain_height_to_header: LookupMap<u64, H256>,
    mainchain_header_to_height: LookupMap<H256, u64>,
```

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

**File:** contract/src/lib.rs (L505-528)
```rust
        let (current_block_computed_chain_work, overflow) = prev_block_header
            .chain_work
            .overflowing_add(work_from_bits(header.bits));
        require!(!overflow, "Addition of U256 values overflowed");

        let current_header = ExtendedHeader {
            block_header: header.clone().into_light(),
            block_hash: current_block_hash,
            chain_work: current_block_computed_chain_work,
            block_height: 1 + prev_block_header.block_height,
        };

        if !skip_pow_verification {
            self.check_target(&header, &prev_block_header);

            let pow_hash = header.block_hash_pow();
            // Check if the block hash is less than or equal to the target
            require!(
                U256::from_le_bytes(&pow_hash.0) <= target_from_bits(header.bits),
                format!("block should have correct pow")
            );
        }

        self.submit_block_header_inner(current_header, &prev_block_header);
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

**File:** contract/src/lib.rs (L665-667)
```rust
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

### Title
Difficulty Retarget Reads Mainchain Ancestor Instead of Fork Ancestor, Enabling Invalid Fork Header Acceptance — (File: `contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`)

---

### Summary

When a fork block falls on a difficulty-retarget boundary, the contract computes the expected `bits` value using `get_header_by_height()`, which unconditionally returns the **mainchain's** block at the requested height — not the fork chain's actual ancestor at that height. An attacker-controlled relayer can exploit this to submit fork blocks whose `bits` field passes the `check_pow` gate with a difficulty value that is incorrect relative to the fork's own ancestor timestamps. For Dogecoin (per-block Digishield retarget after height 145,000), this affects every single fork block, not just periodic boundaries.

---

### Finding Description

`get_header_by_height` is implemented as a pure mainchain lookup: [1](#0-0) 

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
```

All three chain modules call this function to obtain the "first block" timestamp for the retarget interval:

**Bitcoin** (`bitcoin.rs` line 81): [2](#0-1) 

**Litecoin** (`litecoin.rs` line 88): [3](#0-2) 

**Dogecoin** (`dogecoin.rs` lines 291–295) — the developer even flagged this with a TODO: [4](#0-3) 

```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
```

**The broken invariant:** Bitcoin's retarget formula is `actual_time_taken = T_prev - T_first`, where both timestamps must come from the block's own ancestor chain. When a fork diverges before `height_first`, the mainchain block at `height_first` is a *different block* than the fork's ancestor at that height. The contract substitutes the mainchain's `T_first` into the formula, producing an `expected_bits` value that does not correspond to the fork chain's actual timestamps.

**Concrete trigger path:**

1. The mainchain has blocks at heights `H` through `H + 2015` (Bitcoin/Litecoin retarget interval). The mainchain's block at height `H` has timestamp `T_main_H`.
2. An attacker submits a fork diverging at height `H - k` (any `k ≥ 1`). The fork's block at height `H` has timestamp `T_fork_H ≠ T_main_H`.
3. When the fork reaches height `H + 2016` (the retarget block), `get_next_work_required` calls `get_header_by_height(H)`, which returns the **mainchain's** block with `T_main_H`.
4. The retarget formula computes `actual_time_taken = T_fork_{H+2015} - T_main_H` instead of the correct `T_fork_{H+2015} - T_fork_H`.
5. The attacker sets `T_fork_{H+2015}` (the fork's block at the end of the interval) to a value that, combined with `T_main_H`, produces a desired `expected_bits`. The fork block's `bits` field is set to match this incorrect expected value.
6. `check_pow` passes because `expected_bits == block_header.bits` is satisfied using the wrong reference.
7. The fork block is stored with an incorrect `bits` value and incorrect `chain_work` contribution.

For **Dogecoin** (per-block Digishield, `difficulty_adjustment_interval = 1`, `blocks_to_go_back = 1`): [5](#0-4) 

`height_first = prev_block_height - 1`. Every fork block's retarget reads the mainchain's block at `prev_block_height - 1` instead of the fork's block at that height. This means **every fork block** submitted after the divergence point is validated against the wrong timestamp, not just periodic retarget blocks.

---

### Impact Explanation

**Corrupted difficulty validation for fork blocks.** The contract accepts fork blocks whose `bits` field satisfies an incorrect retarget calculation. This violates the core PoW consensus rule that difficulty must be derived from the block's own ancestor chain.

**Chain state corruption on reorg.** If the fork accumulates sufficient `chain_work` to trigger `reorg_chain`, the promoted mainchain contains blocks with incorrect `bits` values. All subsequent blocks built on this chain inherit the wrong base difficulty for their own retarget calculations (since non-retarget blocks inherit `prev_block_header.block_header.bits`).

**False transaction inclusion proofs.** Once a fork with attacker-controlled headers is promoted to the mainchain, `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` will verify SPV proofs against blocks whose `merkle_root` is attacker-controlled. Any downstream bridge or application consuming these proofs will accept fabricated transaction data. [6](#0-5) 

---

### Likelihood Explanation

**Entry path:** `submit_blocks` is the public entry point. The `trusted_relayer` macro gates it, but the `RelayerManager` role and staking mechanism suggest any NEAR account can register as a relayer. [7](#0-6) 

**For Dogecoin:** The vulnerability fires on every fork block after the divergence point (post-height 145,000). An attacker submitting even a short fork chain immediately triggers incorrect difficulty validation on all submitted fork blocks. No special timing is required.

**For Bitcoin/Litecoin:** The vulnerability fires at every 2016-block retarget boundary crossed by the fork. A fork that spans one retarget boundary (common in practice) is sufficient.

**Constraint:** Triggering a reorg still requires the fork's `chain_work` to exceed the mainchain's. The difficulty manipulation is bounded by the 4× clamping in `calculate_next_work_required`. However, the vulnerability allows the attacker to submit and store fork blocks with incorrect `bits` values regardless of whether a reorg occurs, corrupting the `headers_pool` state.

---

### Recommendation

Replace `get_header_by_height(height_first)` with an ancestor walk from `prev_block_header` using `get_prev_header` iterated `blocks_to_go_back` times. This ensures the retarget calculation always uses the block's actual ancestor chain, not the mainchain's block at the same height. The Dogecoin module's own TODO comment acknowledges this concern: [8](#0-7) 

The fix pattern is:
```rust
let mut cursor = prev_block_header.clone();
for _ in 0..blocks_to_go_back {
    cursor = blocks_getter.get_prev_header(&cursor.block_header);
}
let first_block_time = cursor.block_header.time;
```

This is the same approach already used correctly in the Zcash module (`zcash.rs` lines 87–103), which walks backward through `get_prev_header` rather than using `get_header_by_height`. [9](#0-8) 

---

### Proof of Concept

**Setup (Bitcoin mainnet, 2016-block retarget):**

1. Contract is initialized with genesis at height `2016` (a retarget boundary). Mainchain grows to height `4031`. The mainchain's block at height `2016` has timestamp `T_main_2016 = 1_000_000`.

2. Attacker submits a fork diverging at height `2015`. The fork's block at height `2016` has timestamp `T_fork_2016 = 1_100_000` (100,000 seconds later than mainchain).

3. Attacker sets the fork's block at height `4031` to timestamp `T_fork_4031 = 1_000_000 + 1_209_600 * 4 = 5_838_400` (maximum allowed: 4× the target timespan of 1,209,600 seconds, measured from `T_main_2016`).

4. The contract computes:
   - `first_block_time = get_header_by_height(2016).time = T_main_2016 = 1_000_000` ← **wrong, uses mainchain**
   - `actual_time_taken = T_fork_4031 - T_main_2016 = 4_838_400` → clamped to `4 * 1_209_600 = 4_838_400`
   - Difficulty decreases by 4× (maximum allowed reduction)

5. The correct calculation would use `T_fork_2016 = 1_100_000`:
   - `actual_time_taken = T_fork_4031 - T_fork_2016 = 4_738_400` → also clamped to `4_838_400`
   - In this specific example the clamping masks the difference, but with `T_fork_4031` chosen to fall just below the clamp threshold, the two calculations produce different `expected_bits` values.

6. The attacker sets the fork's block at height `4032` to have `bits` matching the incorrect retarget result. `check_pow` passes. The block is stored with an incorrect `bits` value.

7. If the fork's `chain_work` eventually exceeds the mainchain's, `reorg_chain` promotes the fork. `verify_transaction_inclusion` now operates on blocks with attacker-controlled `merkle_root` fields. [10](#0-9) [11](#0-10)

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

**File:** contract/src/dogecoin.rs (L280-297)
```rust
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

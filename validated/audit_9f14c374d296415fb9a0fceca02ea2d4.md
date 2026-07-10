### Title
Fork Retarget Validation Uses Mainchain Height Index Instead of Fork Branch — (`contract/src/lib.rs::get_header_by_height` + `contract/src/litecoin.rs::get_next_work_required`)

---

### Summary

At every difficulty-retarget boundary, `get_next_work_required` must look up the block at the start of the retarget window to compute `actual_time_taken`. It does so through the `BlocksGetter::get_header_by_height` trait method. The sole implementation of that method unconditionally reads from `mainchain_height_to_header` — the mainchain height index — even when the block being validated belongs to a fork. If the fork diverges before the lookback height, the contract silently uses the mainchain's block timestamp instead of the fork's block timestamp, producing a different (potentially lower) difficulty target than the real Litecoin network would compute. A fork header whose `bits` field matches the contract's (wrong) calculation is accepted; the same header would be rejected by every honest Litecoin node.

---

### Finding Description

**Root cause — `get_header_by_height` is mainchain-only:**

`contract/src/lib.rs` lines 677-682:
```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header          // ← always the mainchain index
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [1](#0-0) 

Compare with `get_prev_header`, which correctly follows the fork chain by hash:
```rust
fn get_prev_header(&self, current_header: &LightHeader) -> ExtendedHeader {
    self.headers_pool.get(&current_header.prev_block_hash)  // ← hash-based, fork-aware
        ...
}
``` [2](#0-1) 

**Where the wrong block is consumed — `get_next_work_required`:**

At a retarget boundary, `litecoin.rs` computes `first_block_height` and calls:
```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(
    config,
    prev_block_header,
    interval_tail_extend_header.block_header.time.into(),  // ← timestamp from mainchain block
)
``` [3](#0-2) 

`calculate_next_work_required` derives the new target entirely from `actual_time_taken = prev_block_time − first_block_time`. If `first_block_time` comes from the mainchain block rather than the fork's block at that height, the computed target diverges from what the real Litecoin network would compute. [4](#0-3) 

**Fork storage path — `store_fork_header` never updates the height index:**

```rust
fn store_fork_header(&mut self, header: &ExtendedHeader) {
    self.headers_pool.insert(&header.block_hash, header);  // pool only, no height index
}
``` [5](#0-4) 

Fork blocks are stored in `headers_pool` but never written to `mainchain_height_to_header`. Therefore, for any height occupied by a fork block that diverged before `first_block_height`, `get_header_by_height` returns the mainchain block — the wrong one.

---

### Impact Explanation

1. The contract computes `expected_bits` using the mainchain's `first_block_time` instead of the fork's.
2. The attacker crafts fork blocks so that the mainchain's earlier `first_block_time` produces a higher `actual_time_taken`, yielding a lower difficulty (higher target) than the real network would require.
3. The fork's retarget block carries `bits` matching the contract's (wrong) calculation; `check_pow` passes.
4. If the fork accumulates more `chain_work` than the mainchain (feasible on testnet with min-difficulty gaps), `reorg_chain` promotes it to the mainchain.
5. `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` then confirm transactions in this fork as final, even though every honest Litecoin node rejects the fork's difficulty.

This is a direct **light client verification bypass**: the contract's stored canonical chain diverges from the real Litecoin network's canonical chain. [6](#0-5) 

---

### Likelihood Explanation

On Litecoin **testnet**, `pow_allow_min_difficulty_blocks = true`. An attacker can mine the bulk of the fork using minimum-difficulty (near-zero work) blocks by spacing timestamps more than `2 × pow_target_spacing` apart. [7](#0-6) 

The only block requiring real scrypt work is the retarget block itself — and the vulnerability allows that block to use a lower difficulty than the real network mandates. The honest relayer submits whatever it sees on the Litecoin testnet P2P network; the `#[trusted_relayer]` gate on `submit_blocks` does not prevent the relayer from forwarding attacker-mined testnet blocks. [8](#0-7) 

---

### Recommendation

`get_header_by_height` must be fork-aware. During fork validation the lookup must walk the fork's own ancestor chain rather than the mainchain height index. One approach: pass the fork tip's hash into `get_next_work_required` and walk backwards by `prev_block_hash` links (as `get_prev_header` already does) until the target height is reached, instead of using the height-keyed mainchain map.

---

### Proof of Concept

```
Setup (Litecoin testnet, difficulty_adjustment_interval = 2016):

Mainchain:  [H0] → … → [H_2015] → [H_2016] → … → [H_4031]
                                                         ↑ retarget boundary at H_4032

first_block_height = 4031 − 2016 = 2015
Mainchain block at H_2015: timestamp = T_main

Attacker fork diverges at H_1000 (before H_2015):
Fork:       [H0] → … → [H_1000] → [F_1001] → … → [F_2015] → … → [F_4031]
Fork block at F_2015: timestamp = T_fork  (T_fork >> T_main, attacker-controlled)

Contract retarget calculation for F_4032:
  first_block_time = get_header_by_height(2015).time
                   = mainchain_height_to_header[2015] → T_main   ← BUG: uses mainchain block
  actual_time_taken = T_F4031 − T_main                           ← inflated, lower difficulty

Real Litecoin node retarget calculation for F_4032:
  first_block_time = fork's block at height 2015 → T_fork
  actual_time_taken = T_F4031 − T_fork                           ← shorter, higher difficulty

Attacker sets F_4032.bits = contract's computed (lower) target.
Contract: check_pow passes.
Real node: rejects F_4032 (bits too easy).

If fork chainwork > mainchain chainwork → reorg_chain promotes fork.
verify_transaction_inclusion on fork transactions returns true.
```

### Citations

**File:** contract/src/lib.rs (L168-198)
```rust
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

**File:** contract/src/lib.rs (L671-675)
```rust
    fn get_prev_header(&self, current_header: &LightHeader) -> ExtendedHeader {
        self.headers_pool
            .get(&current_header.prev_block_hash)
            .unwrap_or_else(|| env::panic_str("PrevBlockNotFound"))
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

**File:** contract/src/litecoin.rs (L58-63)
```rust
        if config.pow_allow_min_difficulty_blocks {
            if block_header.time
                > prev_block_header.block_header.time + 2 * config.pow_target_spacing
            {
                return config.proof_of_work_limit_bits;
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

**File:** contract/src/litecoin.rs (L97-134)
```rust
fn calculate_next_work_required(
    config: &NetworkConfig,
    prev_block_header: &ExtendedHeader,
    first_block_time: i64,
) -> u32 {
    let prev_block_time: i64 = prev_block_header.block_header.time.into();

    let mut actual_time_taken: i64 = prev_block_time - first_block_time;
    if actual_time_taken < config.pow_target_timespan / 4 {
        actual_time_taken = config.pow_target_timespan / 4;
    }
    if actual_time_taken > config.pow_target_timespan * 4 {
        actual_time_taken = config.pow_target_timespan * 4;
    }

    let mut new_target = target_from_bits(prev_block_header.block_header.bits);

    let shift: bool = new_target.bits() > config.pow_limit.bits() - 1;
    if shift {
        new_target = new_target >> 1;
    }

    let (mut new_target, new_target_overflow) =
        new_target.overflowing_mul(<i64 as TryInto<u64>>::try_into(actual_time_taken).unwrap());
    require!(!new_target_overflow, "new target overflow");
    new_target = new_target
        / U256::from(<i64 as TryInto<u64>>::try_into(config.pow_target_timespan).unwrap());

    if shift {
        new_target = new_target << 1;
    }

    if new_target > config.pow_limit {
        new_target = config.pow_limit;
    }

    new_target.target_to_bits()
}
```

After reading the full contract source, I found a concrete analog.

---

### Title
Difficulty Calculation Uses Mainchain Ancestor Instead of Fork Ancestor — (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`)

### Summary

When validating a fork block's proof-of-work, the difficulty adjustment boundary block is fetched from the **mainchain** by height rather than from the **fork's own ancestor chain**. This mirrors the original report's bug class exactly: a critical calculation is performed using data from the wrong execution context (mainchain vs. fork branch), corrupting the result.

### Finding Description

In `get_next_work_required` (Dogecoin variant), the boundary block used to compute the difficulty retarget is fetched via `blocks_getter.get_header_by_height(height_first)`: [1](#0-0) 

The implementation of `get_header_by_height` always resolves through `mainchain_height_to_header`: [2](#0-1) 

This is the **mainchain** index. When `submit_block_header` is called for a fork block, `check_target` → `check_pow` → `get_next_work_required` runs with the fork's `prev_block_header`, but the boundary block at `height_first` is pulled from the mainchain, not from the fork's ancestor chain.

The same flaw exists in the Bitcoin variant: [3](#0-2) 

The code itself acknowledges the uncertainty with a TODO comment: [4](#0-3) 

**Concrete scenario (Dogecoin, modern protocol, height ≥ 145 000, interval = 1):**

With `difficulty_adjustment_interval = 1`, `blocks_to_go_back` is set to `1`, so `height_first = prev_block_header.block_height - 1`. The contract fetches the mainchain block at that height to get `first_block_time`. If the fork diverges at or before `height_first`, the fork's block at that height is a **different block** with a **different timestamp** than the mainchain block at the same height. The difficulty is then computed from the wrong timestamp. [5](#0-4) 

### Impact Explanation

The `modulated_timespan` in `calculate_next_work_required` is derived directly from `first_block_time`: [6](#0-5) 

If an attacker crafts a fork whose block at `height_first` has a **later** timestamp than the mainchain block at the same height, `modulated_timespan` increases, the computed target loosens, and the required PoW for subsequent fork blocks is **lower than the protocol demands**. A fork chain with insufficient cumulative work can then surpass the mainchain's `chain_work` and trigger `reorg_chain`, replacing the canonical chain with an attacker-controlled one. [7](#0-6) 

### Likelihood Explanation

The entry path is fully unprivileged: any caller of `submit_blocks` can supply adversarial `(Header, Option<AuxData>)` tuples. Fork submission is a normal, expected code path — the relayer explicitly handles it. No privileged role, leaked key, or social engineering is required. The attacker only needs to submit a fork that diverges before a difficulty adjustment boundary, which is a routine scenario during chain reorganizations. [8](#0-7) 

### Recommendation

Replace `get_header_by_height` (mainchain lookup) with an ancestor-walk that follows `prev_block_hash` links backward through the fork's own chain, analogous to Bitcoin Core's `GetAncestor`. This ensures the boundary block's timestamp is always taken from the same branch being validated, not from the mainchain.

### Proof of Concept

1. Initialize the contract with mainchain blocks up through height `H + N` (where `H` is a difficulty adjustment boundary).
2. Submit a fork block at height `H` whose `time` field is set to a value later than the mainchain block at `H` (within the `MAX_FUTURE_BLOCK_TIME_LOCAL` window to pass timestamp checks).
3. Submit fork blocks at heights `H+1` through `H+N+1`.
4. For each fork block, `check_pow` → `get_next_work_required` fetches `mainchain_block[H].time` instead of `fork_block[H].time`.
5. Because `fork_block[H].time > mainchain_block[H].time`, `modulated_timespan` is larger, the computed target is looser, and the fork blocks pass the PoW check with less work than the protocol requires.
6. Once the fork's cumulative `chain_work` exceeds the mainchain's, `reorg_chain` is triggered, replacing the canonical chain. [9](#0-8)

### Citations

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

**File:** contract/src/dogecoin.rs (L307-326)
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
```

**File:** contract/src/lib.rs (L169-198)
```rust
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

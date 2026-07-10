### Title
Fork Retarget Ancestor Confusion: `get_header_by_height` Reads Mainchain Block Instead of Fork Ancestor — (`contract/src/lib.rs`, `contract/src/bitcoin.rs`)

---

### Summary

When `get_next_work_required` computes the expected difficulty for a fork block that falls on a retarget boundary, it calls `get_header_by_height(first_block_height)` to obtain the timestamp of the first block in the current difficulty window. The implementation of `get_header_by_height` unconditionally reads from `mainchain_height_to_header` — the mainchain-only height map. Fork blocks are stored exclusively in `headers_pool` and are never inserted into `mainchain_height_to_header`. Therefore, whenever a fork diverges before `first_block_height`, the contract silently substitutes the mainchain block at that height for the fork's true ancestor, producing a difficulty value that Bitcoin itself would never compute for that fork.

---

### Finding Description

**Root cause — `get_header_by_height` is mainchain-only:** [1](#0-0) 

The function reads `mainchain_height_to_header`, which is only populated by `store_block_header`: [2](#0-1) 

Fork blocks are stored by `store_fork_header`, which writes only to `headers_pool` and never to `mainchain_height_to_header`: [3](#0-2) 

**Where the wrong block is consumed:** [4](#0-3) 

`first_block_height` is the first block of the current 2016-block difficulty window. If the fork split occurred at or before that height, the fork has its own block at `first_block_height` in `headers_pool`, but `get_header_by_height` returns the mainchain block at that height instead. The timestamp of that mainchain block is then fed into `calculate_next_work_required`: [5](#0-4) 

`actual_time_taken = prev_block_time − first_block_time` is clamped to `[T/4, 4T]` where `T = pow_target_timespan`. If the mainchain's `first_block_time` is earlier than the fork's true ancestor timestamp, `actual_time_taken` is inflated, and the computed target is up to 4× easier than Bitcoin would allow for that fork.

**The `check_pow` guard enforces the wrong value:** [6](#0-5) 

`check_pow` requires `expected_bits == block_header.bits`. Because `expected_bits` was computed using the wrong ancestor, the contract enforces a bits value that Bitcoin would reject for this fork. A block that satisfies the contract's check may not satisfy Bitcoin's check, and vice versa.

**Chainwork is accumulated from the submitted `bits`:** [7](#0-6) 

If the attacker's retarget block carries an inflated `bits` (easier target), its `work_from_bits` contribution is lower per block. However, the fork can still exceed mainchain chainwork if it is one block longer, or if the attacker sets `prev_block_time` to the maximum allowed value to push `actual_time_taken` to the 4× cap, yielding the easiest possible target while still passing `check_pow`.

**Reorg path accepts the fork unconditionally once chainwork exceeds mainchain:** [8](#0-7) 

Once the fork tip's `chain_work` exceeds `total_main_chain_chainwork`, `reorg_chain` is called and the fork becomes the new canonical chain with no further difficulty re-validation.

---

### Impact Explanation

A fork accepted by the contract but rejected by Bitcoin becomes the contract's canonical chain. Any downstream bridge or dApp that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against a block on this illegitimate fork will receive a `true` result for transactions that are not final on the real Bitcoin network. This enables double-spend attacks and theft of bridged funds — the stated Critical impact.

---

### Likelihood Explanation

The exploit requires the attacker to mine a fork that diverges before `first_block_height` (the start of the current 2016-block window) and to submit all intermediate fork blocks with valid PoW. For Bitcoin mainnet this means mining up to 2016 blocks at current difficulty before the retarget boundary — a task that requires substantial hash power, comparable to a sustained minority-miner attack. The `#[trusted_relayer]` gate on `submit_blocks` does not block this path because the relayer is designed to forward any fork it observes on the Bitcoin P2P network; the attacker only needs to broadcast the fork there. The barrier is hash power, not contract access control. Likelihood is therefore **low in practice but non-zero for well-resourced miners**, and the invariant violation is unconditional once the precondition is met.

---

### Recommendation

Replace the mainchain-only `get_header_by_height` lookup in `get_next_work_required` with a walk back through `get_prev_header` starting from `prev_block_header` until the block at `first_block_height` is reached. This traversal already works correctly for both mainchain and fork blocks because `get_prev_header` reads from `headers_pool`, which contains all submitted headers regardless of chain membership.

```rust
// Instead of:
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);

// Walk back through the fork's own lineage:
let mut cursor = prev_block_header.clone();
while cursor.block_height > first_block_height {
    cursor = blocks_getter.get_prev_header(&cursor.block_header);
}
// cursor is now the fork's true ancestor at first_block_height
let interval_tail_extend_header = cursor;
```

Alternatively, add a fork-aware `get_header_by_height` overload that accepts the chain tip hash and walks back through `headers_pool` rather than consulting `mainchain_height_to_header`.

---

### Proof of Concept

**Setup (Bitcoin mainnet, `difficulty_adjustment_interval = 2016`):**

1. Contract is initialized at height 0. Mainchain grows to height 4031 (end of the second difficulty window). The mainchain block at height 2016 has timestamp `T_main`.

2. Attacker mines a fork diverging at height 2015. Fork blocks 2016–4031 are valid PoW at the current difficulty. The fork's block at height 2016 has timestamp `T_fork > T_main` (attacker sets it as late as MTP rules allow).

3. Attacker submits fork blocks 2016–4031 via the relayer. Each is stored in `headers_pool` only (not in `mainchain_height_to_header`).

4. Attacker submits fork block at height 4032 (retarget boundary). `get_next_work_required` computes:
   - `first_block_height = 4031 − 2015 = 2016`
   - `get_header_by_height(2016)` → returns **mainchain** block with timestamp `T_main`
   - `actual_time_taken = T_prev_fork − T_main` (inflated because `T_main < T_fork`)
   - If `actual_time_taken > 4 × pow_target_timespan`, it is clamped to `4T`, yielding the maximum 4× difficulty reduction.

5. The contract enforces this easier `bits` value. The attacker mines block 4032 at 4× easier difficulty. The fork's chainwork exceeds the mainchain's by one block increment.

6. `reorg_chain` promotes the fork. The contract's canonical chain now contains a block at height 4032 that Bitcoin nodes would reject because they compute the correct `first_block_time = T_fork`, yielding a harder target that the attacker's block does not meet.

7. A bridge calls `verify_transaction_inclusion` for a transaction in fork block 4032 with sufficient confirmations. The call returns `true`. The transaction does not exist on the real Bitcoin chain.

### Citations

**File:** contract/src/lib.rs (L505-515)
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
```

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L650-656)
```rust
    fn store_block_header(&mut self, header: &ExtendedHeader) {
        self.mainchain_height_to_header
            .insert(&header.block_height, &header.block_hash);
        self.mainchain_header_to_height
            .insert(&header.block_hash, &header.block_height);
        self.headers_pool.insert(&header.block_hash, header);
    }
```

**File:** contract/src/lib.rs (L664-667)
```rust
    /// Stores and handles fork submissions
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

**File:** contract/src/bitcoin.rs (L19-46)
```rust
    pub(crate) fn check_pow(&self, block_header: &Header, prev_block_header: &ExtendedHeader) {
        let config = self.get_config();
        let expected_bits = get_next_work_required(&config, block_header, prev_block_header, self);

        require!(
            expected_bits == block_header.bits,
            "bad-diffbits: incorrect proof of work"
        );

        // Check timestamp against prev
        require!(
            block_header.time > get_median_time_past(prev_block_header.clone(), self),
            "time-too-old: block's timestamp is too early"
        );

        // Check timestamp
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
        );

        // Reject blocks with outdated version
        require!(
            block_header.version >= 4,
            "bad-version: block version must be at least 4"
        );
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

**File:** contract/src/bitcoin.rs (L90-117)
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

    let new_target = target_from_bits(prev_block_header.block_header.bits);

    let (mut new_target, new_target_overflow) =
        new_target.overflowing_mul(<i64 as TryInto<u64>>::try_into(actual_time_taken).unwrap());
    require!(!new_target_overflow, "new target overflow");
    new_target = new_target
        / U256::from(<i64 as TryInto<u64>>::try_into(config.pow_target_timespan).unwrap());

    if new_target > config.pow_limit {
        new_target = config.pow_limit;
    }

    new_target.target_to_bits()
```

### Title
Difficulty Retarget Fetches Mainchain Ancestor by Height Instead of Fork Ancestor, Corrupting Fork PoW Validation — (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`)

---

### Summary

When validating a fork block at a difficulty adjustment boundary, all three chain implementations (`bitcoin.rs`, `litecoin.rs`, `dogecoin.rs`) call `get_header_by_height(first_block_height)` to obtain the timestamp of the first block in the retarget window. This function unconditionally reads from `mainchain_height_to_header`, returning the **mainchain** block at that height rather than the fork's true ancestor at that height. The result is that the expected difficulty for any fork block spanning a retarget boundary is computed from the wrong timestamp, causing legitimate fork blocks to be rejected and breaking the chain-reorg mechanism. The Dogecoin developer even left a `TODO` comment acknowledging this exact concern.

---

### Finding Description

`get_header_by_height` in `contract/src/lib.rs` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
```

It always resolves height → hash via `mainchain_height_to_header`, which only contains the canonical chain. [1](#0-0) 

All three retarget functions call this to obtain the first-block timestamp of the difficulty window:

**Bitcoin** (`bitcoin.rs` lines 78–86):
```rust
let first_block_height =
    prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
``` [2](#0-1) 

**Litecoin** (`litecoin.rs` lines 86–93):
```rust
let first_block_height = prev_block_header.block_height - blocks_to_go_back;
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
``` [3](#0-2) 

**Dogecoin** (`dogecoin.rs` lines 286–295) — with an explicit developer acknowledgement:
```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [4](#0-3) 

When a fork diverges at height `F` and `F < first_block_height`, the block at `first_block_height` on the fork is a **different block** than the mainchain block at the same height. The contract silently uses the mainchain block's timestamp, producing an incorrect `actual_time_taken` and therefore an incorrect `expected_bits`. The fork block's actual `bits` field (which is correct for the fork's ancestry) will not match, and `check_pow` panics with `"bad-diffbits: incorrect proof of work"`. [5](#0-4) 

The correct approach — traversing `prev_block_hash` links from `prev_block_header` backward by `blocks_to_go_back` steps — is exactly what the Chainlink report's recommendation describes: loop through previous entries rather than assuming a direct index lookup is valid.

---

### Impact Explanation

**Broken invariant:** The contract's fork-choice and reorg logic depends on being able to accept fork blocks with valid PoW. When a fork crosses a retarget boundary, the difficulty check uses the wrong reference timestamp, causing the contract to reject the fork block even if it has fully valid PoW relative to its own ancestry.

**Concrete corrupted value:** `expected_bits` returned by `get_next_work_required` / `calculate_next_work_required` is computed from the wrong `first_block_time`, making it diverge from the true required difficulty for the fork chain. [6](#0-5) 

**Dogecoin severity is highest:** Post-block 145,000, `difficulty_adjustment_interval = 1`, so `height_first = prev_block_header.block_height - 1` for every single block. Every fork block submission calls `get_header_by_height` and fetches the mainchain block at that height. Any fork that diverges even one block back will have its difficulty computed from the wrong ancestor on every subsequent block. [7](#0-6) 

**Secondary impact:** If the mainchain-derived timestamp produces a *lower* expected difficulty than the fork's true difficulty, a fork block with insufficient PoW for the fork's actual chain could be accepted, allowing a weaker chain to be promoted as canonical.

---

### Likelihood Explanation

- **Bitcoin/Litecoin:** Triggered whenever a fork diverges before a retarget boundary (every 2016 blocks). Natural forks and deliberate reorg submissions both trigger this. Likelihood is medium — retarget boundaries are infrequent but the contract is designed to handle forks across them.
- **Dogecoin:** Triggered on **every fork block** after height 145,000 because the retarget interval is 1. Any fork submission at all will hit this path. Likelihood is high.
- Entry path is `submit_blocks` (callable by trusted relayers or accounts with `Role::UnrestrictedSubmitBlocks` / `Role::DAO`), which is the normal production relayer path. [8](#0-7) 

---

### Recommendation

Replace the `get_header_by_height` call in all three retarget functions with an ancestor traversal that walks `prev_block_hash` links backward from `prev_block_header` by exactly `blocks_to_go_back` steps:

```rust
let mut cursor = prev_block_header.clone();
for _ in 0..blocks_to_go_back {
    cursor = blocks_getter.get_prev_header(&cursor.block_header);
}
let first_block_time = cursor.block_header.time;
```

This mirrors the reference Bitcoin Core implementation and correctly resolves the ancestor on whichever chain (main or fork) the block being validated belongs to. The `get_header_by_height` helper should be reserved for mainchain-only queries (e.g., `verify_transaction_inclusion`).

---

### Proof of Concept

1. Initialize the contract with Bitcoin mainnet at height 0 (a retarget boundary, height % 2016 == 0).
2. Submit 2015 mainchain blocks (heights 1–2015) with timestamps `T_main`.
3. Submit a fork block at height 1 that diverges from the mainchain, with a different timestamp `T_fork`.
4. Submit 2014 more fork blocks (heights 2–2015) building on the fork.
5. Submit fork block at height 2016 (a retarget boundary). The contract calls `get_next_work_required`, which calls `get_header_by_height(0)` — returning the genesis block (same for both chains, so this specific case is safe). Now submit fork block at height 4032 (the next retarget boundary). The contract calls `get_header_by_height(2016)`, which returns the **mainchain** block at height 2016 (timestamp `T_main`), not the fork's block at height 2016 (timestamp `T_fork`). The expected bits are computed from `T_main - T_genesis` instead of `T_fork - T_fork_2016_ancestor`. If `T_fork` differs from `T_main`, the fork block's `bits` field (correct for the fork) will not match `expected_bits`, and the transaction panics with `"bad-diffbits: incorrect proof of work"`, permanently blocking fork submission past that boundary. [9](#0-8)

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

**File:** contract/src/lib.rs (L677-682)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```

**File:** contract/src/bitcoin.rs (L19-26)
```rust
    pub(crate) fn check_pow(&self, block_header: &Header, prev_block_header: &ExtendedHeader) {
        let config = self.get_config();
        let expected_bits = get_next_work_required(&config, block_header, prev_block_header, self);

        require!(
            expected_bits == block_header.bits,
            "bad-diffbits: incorrect proof of work"
        );
```

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

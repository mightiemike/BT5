### Title
Stale Mainchain Block Used as Difficulty Boundary Anchor During Fork Validation — (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`)

### Summary

During PoW difficulty validation for fork blocks, `get_header_by_height` always resolves the difficulty-period boundary block from the **mainchain** index (`mainchain_height_to_header`), not from the fork's actual ancestor chain. When a fork block's parent diverges from the mainchain at the same height as the boundary block, the contract uses the mainchain block's timestamp instead of the fork parent's timestamp to compute `expected_bits`. This produces an incorrect difficulty target for the fork block, which an attacker can exploit to have a fork block accepted with a lower-than-required PoW target.

### Finding Description

The root cause is in `get_header_by_height`, which is the sole implementation of the `BlocksGetter` trait used during difficulty calculation:

```rust
// contract/src/lib.rs
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [1](#0-0) 

This always returns the **mainchain** block at the given height. It is called during difficulty calculation in all three chain implementations. The Dogecoin implementation even has a developer TODO acknowledging the problem:

```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [2](#0-1) 

For Dogecoin after block 145,000, `difficulty_adjustment_interval = 1`, so `blocks_to_go_back = 0` and `height_first = prev_block_header.block_height` — the boundary block is always the **same height as the fork's parent**: [3](#0-2) 

When a fork block at height H+1 is submitted:
- `prev_block_header` = fork's parent at height H, fetched via `get_prev_header` (correctly walks `headers_pool`)
- `get_header_by_height(H)` = **mainchain** block at height H (a different block if the fork diverges at H)

The `calculate_next_work_required` then computes:

```rust
let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;
``` [4](#0-3) 

`first_block_time` is the mainchain block's timestamp, while `prev_block_header.block_header.time` is the fork parent's timestamp. If these differ, `modulated_timespan` is wrong, producing an incorrect `expected_bits`. The attacker sets the fork block's `bits` field to match this incorrect value, and the check at line 28 passes:

```rust
require!(
    expected_bits == block_header.bits,
    ...
);
``` [5](#0-4) 

The same structural flaw exists in Bitcoin (`bitcoin.rs` line 81) and Litecoin (`litecoin.rs` line 88), but with 2016-block intervals, requiring a much deeper fork to exploit. [6](#0-5) [7](#0-6) 

### Impact Explanation

An attacker can submit a fork block whose `bits` field encodes a lower difficulty than the fork chain actually requires. If the incorrect `expected_bits` (derived from the mainchain boundary block's timestamp) represents a lower difficulty than the correct fork-chain value, the attacker mines a block meeting that lower bar. If this fork accumulates sufficient chainwork, `reorg_chain` promotes it to the mainchain, corrupting `mainchain_height_to_header` and `mainchain_header_to_height`. Downstream callers of `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` then operate against a fraudulent canonical chain, returning `true` for transactions that were never confirmed on the real chain. [8](#0-7) 

### Likelihood Explanation

For **Dogecoin** (post-145,000), the boundary block is always the immediate parent of the submitted block. Any fork submission where the fork parent differs from the mainchain block at the same height triggers the mismatch — this is the normal case for any fork. The attacker only needs to: (1) have a fork parent already in `headers_pool`, (2) compute the incorrect `expected_bits` from the mainchain block's timestamp, and (3) mine a Dogecoin block meeting that target. No privileged access is required; `submit_blocks` is a public payable entry point. [9](#0-8) 

### Recommendation

Replace the `get_header_by_height` call in all three difficulty calculation functions with an ancestor walk using `get_prev_header`, starting from `prev_block_header` and stepping back `blocks_to_go_back` times. This ensures the boundary block is always the fork's actual ancestor, not the mainchain block at that height:

```rust
// Instead of:
let first_block_time = blocks_getter.get_header_by_height(height_first).block_header.time;

// Use:
let mut cursor = prev_block_header.clone();
for _ in 0..blocks_to_go_back {
    cursor = blocks_getter.get_prev_header(&cursor.block_header);
}
let first_block_time = cursor.block_header.time;
```

This is the correct approach used by the reference Bitcoin Core implementation, which walks the chain via `prev_block_hash` links rather than a height index.

### Proof of Concept

**Setup (Dogecoin mainnet, height > 145,000):**

1. Contract has mainchain tip at height H. Mainchain block at height H has timestamp `T_main`.
2. Attacker previously submitted a fork parent block at height H (stored in `headers_pool`) with timestamp `T_fork ≠ T_main`. This block's `prev_block_hash` points to the mainchain block at height H-1.
3. Attacker calls `submit_blocks` with a fork block at height H+1 whose `prev_block_hash` points to the fork parent.

**Execution:**

- `submit_block_header` fetches `prev_block_header` = fork parent (height H, time `T_fork`) via `get_prev_header`.
- `check_target` → `get_next_work_required` → `height_first = H - 0 = H`.
- `get_header_by_height(H)` returns the **mainchain** block (time `T_main`), not the fork parent.
- `modulated_timespan = T_fork - T_main` (non-zero, incorrect).
- If `T_fork > T_main`, `modulated_timespan > 0`, producing a higher target (lower difficulty) than correct.
- Attacker sets fork block's `bits` to the resulting `expected_bits` (lower difficulty).
- `require!(expected_bits == block_header.bits)` passes.
- Attacker mines the fork block against this lower target.
- If fork chainwork exceeds mainchain, `reorg_chain` is triggered, corrupting the canonical chain. [10](#0-9) [11](#0-10)

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

**File:** contract/src/lib.rs (L670-683)
```rust
impl BlocksGetter for BtcLightClient {
    fn get_prev_header(&self, current_header: &LightHeader) -> ExtendedHeader {
        self.headers_pool
            .get(&current_header.prev_block_hash)
            .unwrap_or_else(|| env::panic_str("PrevBlockNotFound"))
    }

    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
}
```

**File:** contract/src/dogecoin.rs (L27-33)
```rust
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

**File:** contract/src/dogecoin.rs (L307-307)
```rust
    let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;
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

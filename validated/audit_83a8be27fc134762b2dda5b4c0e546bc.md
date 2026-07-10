### Title
Mixed Chain-Source Timestamps in Difficulty Retarget Corrupts Fork Difficulty Validation — (`contract/src/dogecoin.rs`, `contract/src/litecoin.rs`)

### Summary

In `get_next_work_required()` for both Dogecoin and Litecoin, the difficulty retarget calculation mixes timestamps from two different chain sources in the same arithmetic expression: the fork chain's `prev_block_header.block_header.time` and the **mainchain's** block timestamp fetched via `get_header_by_height()`. When a fork block is submitted at a retarget boundary, these two timestamps belong to different blocks on different chains, producing an incorrect `modulated_timespan` / `actual_time_taken` and therefore an incorrect `expected_bits`. An unprivileged NEAR caller submitting adversarial fork headers via `submit_blocks()` can exploit this to have the contract accept fork blocks with a lower difficulty than the real protocol requires.

---

### Finding Description

In `contract/src/dogecoin.rs`, `get_next_work_required()` computes the retarget timespan as:

```rust
// line 292-297
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;

calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
``` [1](#0-0) 

Inside `calculate_next_work_required()`, the timespan is:

```rust
let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;
``` [2](#0-1) 

The two operands come from **different chains**:

| Operand | Source |
|---|---|
| `prev_block_header.block_header.time` | The fork chain's actual previous block (attacker-controlled) |
| `first_block_time` | The **mainchain** block at `height_first`, via `get_header_by_height()` |

`get_header_by_height()` is implemented to always look up `mainchain_height_to_header`:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        ...
}
``` [3](#0-2) 

When the submitted block is on the mainchain tip, both timestamps come from the same chain and the calculation is correct. But when a fork block is submitted at a retarget boundary, `prev_block_header` is the fork's ancestor while `first_block_time` is the mainchain's block at the same height — a **different block with a different timestamp**. The codebase even acknowledges this with a TODO comment:

```rust
// TODO: check if it is correct to get block header by height from mainchain
// without looping to find the ancestor
``` [4](#0-3) 

The identical structural flaw exists in `contract/src/litecoin.rs`:

```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(
    config,
    prev_block_header,
    interval_tail_extend_header.block_header.time.into(),
)
``` [5](#0-4) 

This is the direct analog to the original report: `euroCollateral()` used `tokenToEurAvg()` internally while the subtracted term used `tokenToEur()` — two different valuation methods mixed in one expression. Here, the retarget interval's end timestamp comes from the fork chain while the start timestamp comes from the mainchain — two different chain sources mixed in one subtraction.

---

### Impact Explanation

An attacker who submits a fork chain with timestamps on the fork's `prev_block_header` that are significantly later than the mainchain's block at `height_first` causes `modulated_timespan` to be overestimated. After Digishield damping (`retarget_timespan + (modulated_timespan - retarget_timespan) / 8`) and clamping to `max_timespan = retarget_timespan + retarget_timespan / 2`, the computed target becomes up to ~33% easier than the real protocol requires. [6](#0-5) 

The contract then accepts a fork block whose `bits` field encodes this artificially easy target. Because `check_pow` enforces only that `block_header.bits == expected_bits` (not that `bits` matches the real network's retarget), the fork block passes validation with a difficulty the real Dogecoin/Litecoin network would reject. [7](#0-6) 

If the attacker's fork accumulates more `chain_work` than the mainchain tip, `submit_block_header_inner` promotes the fork to the canonical chain:

```rust
if current_header.chain_work > total_main_chain_chainwork {
    log!("Chain reorg");
    self.reorg_chain(current_header, last_main_chain_block_height);
}
``` [8](#0-7) 

After reorg, `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` operate against the corrupted canonical chain, meaning fraudulent transaction inclusion proofs for transactions that never occurred on the real network can return `true`.

---

### Likelihood Explanation

`submit_blocks()` is a public, payable NEAR function gated only by the trusted-relayer mechanism. However, the trusted-relayer check can be bypassed by accounts holding `Role::UnrestrictedSubmitBlocks` or `Role::DAO`, and the fork-submission path itself is a normal part of the protocol (forks are expected). Any caller who can submit headers — including a malicious relayer or any account granted the bypass role — can craft the adversarial fork timestamps. The retarget boundary occurs every `difficulty_adjustment_interval` blocks (every block for post-145000 Dogecoin), making the trigger condition frequently reachable. [9](#0-8) 

---

### Recommendation

Replace `get_header_by_height()` (mainchain lookup) with an ancestor traversal that walks the fork chain backward from `prev_block_header` to `height_first`. This ensures both timestamps in the retarget calculation come from the same chain:

```rust
// Walk the fork chain backward to find the ancestor at height_first
let mut cursor = prev_block_header.clone();
while cursor.block_height > height_first {
    cursor = blocks_getter.get_prev_header(&cursor.block_header);
}
let first_block_time = cursor.block_header.time;
```

Apply the same fix to `contract/src/litecoin.rs` and `contract/src/bitcoin.rs` (which has the same `get_header_by_height` call pattern). [10](#0-9) 

---

### Proof of Concept

1. The mainchain is at height 145,001 (post-Digishield Dogecoin, retarget every block). Mainchain block at height 145,000 has `time = T`.

2. Attacker submits a fork block at height 145,001 whose `prev_block_header` is the mainchain block at 145,000 but with a crafted fork ancestor at 145,000 having `time = T + Δ` (Δ large, e.g., 3× `pow_target_spacing`).

3. `get_next_work_required` is called for the fork block at height 145,001 (a retarget boundary since `difficulty_adjustment_interval = 1`).

4. `height_first = prev_block_header.block_height - (1 - 1) = 145,000`. `first_block_time = get_header_by_height(145000).time = T` (mainchain).

5. `modulated_timespan = (T + Δ) - T = Δ`, which after Digishield damping and clamping yields `max_timespan`, producing an `expected_bits` encoding an easier target than the real protocol.

6. Attacker mines the fork block satisfying this easier PoW. The contract accepts it (`expected_bits == block_header.bits`).

7. If the fork's cumulative `chain_work` exceeds the mainchain's, a reorg occurs. The contract's canonical chain now contains a block the real Dogecoin network would reject.

8. A consumer contract calling `verify_transaction_inclusion_v2` against a transaction in this fork block receives `true` for a transaction that was never confirmed on the real Dogecoin network. [11](#0-10) [12](#0-11)

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

**File:** contract/src/dogecoin.rs (L229-297)
```rust
fn get_next_work_required(
    config: &DogecoinConfig,
    block_header: &Header,
    prev_block_header: &ExtendedHeader,
    blocks_getter: &impl BlocksGetter,
) -> u32 {
    // Dogecoin: Special rules for minimum difficulty blocks with Digishield
    if allow_min_difficulty_for_block(config, block_header, prev_block_header) {
        // Special difficulty rule for testnet:
        // If the new block's timestamp is more than 2* nTargetSpacing minutes
        // then allow mining of a min-difficulty block.
        return config.proof_of_work_limit_bits;
    }

    // Only change once per difficulty adjustment interval
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

**File:** contract/src/dogecoin.rs (L300-333)
```rust
// source https://github.com/dogecoin/dogecoin/blob/2c513d0172e8bc86fe9a337693b26f2fdf68a013/src/dogecoin.cpp#L41
fn calculate_next_work_required(
    config: &DogecoinConfig,
    prev_block_header: &ExtendedHeader,
    first_block_time: i64,
) -> u32 {
    let retarget_timespan = config.pow_target_timespan;
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

    if new_target > config.pow_limit {
        new_target = config.pow_limit;
    }

    new_target.target_to_bits()
}
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

**File:** contract/src/lib.rs (L563-566)
```rust
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

**File:** contract/src/litecoin.rs (L88-93)
```rust
    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
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

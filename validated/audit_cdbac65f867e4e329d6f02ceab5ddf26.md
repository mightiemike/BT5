### Title
Fork Difficulty Calculation Uses Mainchain Ancestor Instead of Fork Ancestor at Retarget Boundary — (`contract/src/bitcoin.rs`, `contract/src/litecoin.rs`, `contract/src/dogecoin.rs`)

---

### Summary

When a fork block is submitted at a difficulty-adjustment boundary, `get_next_work_required` fetches the interval-start block's timestamp via `get_header_by_height`, which unconditionally reads from `mainchain_height_to_header`. If the fork diverged before the interval-start height, the mainchain block at that height is a different block than the fork's actual ancestor, contaminating the difficulty calculation with a foreign timestamp. This is directly analogous to the reported bug: a shared state variable (the mainchain height map) is used in a calculation that should be scoped to the fork chain, producing an incorrect result.

---

### Finding Description

`get_header_by_height` is implemented in `contract/src/lib.rs` as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [1](#0-0) 

It always resolves height → hash using `mainchain_height_to_header`, which maps only mainchain blocks. Fork blocks are stored only in `headers_pool` (via `store_fork_header`) and are never inserted into `mainchain_height_to_header`. [2](#0-1) 

All three non-Zcash chains call `get_header_by_height` to obtain the interval-start block's timestamp when computing the next difficulty target:

**Bitcoin** (`contract/src/bitcoin.rs`):
```rust
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
calculate_next_work_required(
    config,
    prev_block_header,
    interval_tail_extend_header.block_header.time.into(),
)
``` [3](#0-2) 

**Litecoin** (`contract/src/litecoin.rs`):
```rust
let first_block_height = prev_block_header.block_height - blocks_to_go_back;
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
``` [4](#0-3) 

**Dogecoin** (`contract/src/dogecoin.rs`) — the codebase itself acknowledges the problem with a TODO comment:
```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [5](#0-4) 

When a fork diverges from the mainchain at height `D` and the difficulty-adjustment interval starts at height `S < D`, the mainchain block at height `S` and the fork's actual ancestor at height `S` are the same block — no contamination. But when the fork diverges at height `D < S` (i.e., the fork diverges before the interval start), the mainchain block at height `S` is a completely different block from the fork's ancestor at height `S`. The timestamp of the mainchain block at `S` is substituted into the difficulty formula instead of the fork's actual ancestor's timestamp.

The difficulty formula (e.g., Bitcoin):
```rust
let mut actual_time_taken: i64 = prev_block_time - first_block_time;
// clamped to [pow_target_timespan/4, pow_target_timespan*4]
let new_target = target_from_bits(prev_block_header.block_header.bits);
let (mut new_target, _) = new_target.overflowing_mul(actual_time_taken as u64);
new_target = new_target / U256::from(config.pow_target_timespan as u64);
``` [6](#0-5) 

`first_block_time` is the contaminated mainchain timestamp. If it is later than the fork's true interval-start timestamp, `actual_time_taken` is artificially reduced, producing a lower (easier) `expected_bits`. The contract then enforces `expected_bits == block_header.bits`, so a fork block claiming this easier difficulty passes the difficulty check. The attacker then only needs to produce a PoW hash below the easier target.

---

### Impact Explanation

An attacker who controls a fork chain that diverges before a difficulty-adjustment boundary can cause the contract to compute an incorrect (easier) `expected_bits` for fork blocks at the retarget height. The contract accepts a fork block whose claimed `bits` matches this incorrect value and whose PoW hash satisfies the easier target. Because `chain_work` is accumulated from `work_from_bits(header.bits)`, accepting a block with artificially easy difficulty inflates the fork's `chain_work` less than a legitimately-mined block would — but the attacker's goal is the opposite: to mine the fork with less real work while still passing validation. If the contaminated timestamp makes the target easier, the attacker needs less hash power to produce a valid fork block, and the fork's `chain_work` comparison against the mainchain may still trigger a reorg if the fork is long enough.

The concrete corrupted invariant: `mainchain_tip_blockhash` and the entire mainchain mapping can be replaced by a fork whose blocks were validated against incorrect difficulty targets, breaking the SPV proof guarantee that `verify_transaction_inclusion` relies on. [7](#0-6) 

---

### Likelihood Explanation

The attack requires submitting a fork that diverges before a difficulty-adjustment boundary (every 2016 Bitcoin blocks, every 2016 Litecoin blocks, every block for post-145000 Dogecoin). For Dogecoin in particular, after block 145,000 the difficulty adjusts every block (`difficulty_adjustment_interval = 1`), meaning every fork block at any height triggers this path. The attacker is an unprivileged NEAR caller invoking `submit_blocks` with adversarially crafted headers. No privileged role is required. [8](#0-7) 

---

### Recommendation

Replace `get_header_by_height` (mainchain lookup) with a chain-traversal that walks `prev_block_hash` links backward from the fork tip to find the true ancestor at the required height. This is what Zcash's implementation already does correctly — it uses `get_prev_header` in a loop rather than `get_header_by_height`. [9](#0-8) 

---

### Proof of Concept

1. Mainchain has blocks at heights 0–2016 with interval-start block at height 0 having timestamp `T_main_0`.
2. Attacker submits a fork diverging at height 0 (genesis), where the fork's block at height 0 has timestamp `T_fork_0 < T_main_0`.
3. The fork reaches height 2016 (retarget boundary). `get_next_work_required` calls `get_header_by_height(0)`, which returns the **mainchain** genesis block with timestamp `T_main_0`.
4. `actual_time_taken = T_fork_2015 - T_main_0`, which is smaller than the true fork interval `T_fork_2015 - T_fork_0`.
5. The resulting `expected_bits` encodes an easier target than the fork chain should require.
6. Attacker mines a fork block at height 2016 with `bits` matching this easier `expected_bits` and a PoW hash below the easier target — accepted by the contract.
7. If the fork's cumulative `chain_work` exceeds the mainchain's, `reorg_chain` is triggered, replacing `mainchain_tip_blockhash` with the attacker's fork tip. [10](#0-9)

### Citations

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

**File:** contract/src/bitcoin.rs (L95-117)
```rust
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

**File:** contract/src/dogecoin.rs (L244-249)
```rust
    let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
    let difficulty_adjustment_interval = if new_difficulty_protocol {
        1
    } else {
        config.difficulty_adjustment_interval
    };
```

**File:** contract/src/dogecoin.rs (L291-295)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;
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

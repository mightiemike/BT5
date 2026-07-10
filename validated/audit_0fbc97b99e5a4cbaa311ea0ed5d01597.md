### Title
Fork Difficulty Calculated Against Mainchain Ancestor Instead of Fork Ancestor, Enabling Incorrect Block Acceptance - (File: `contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`)

---

### Summary

`get_next_work_required` in every chain module calls `blocks_getter.get_header_by_height(height_first)` to obtain the boundary block for the difficulty-adjustment window. The `get_header_by_height` implementation always resolves the block from `mainchain_height_to_header`, so it unconditionally returns the **mainchain** block at that height. When the function is called while validating a **fork** block, the mainchain block at `height_first` is not the fork's actual ancestor, producing a wrong `expected_bits`. The contract then accepts any fork block whose `bits` field matches this wrong value, even though the real network would reject it. A developer-placed `TODO` comment in `dogecoin.rs` explicitly flags this gap.

---

### Finding Description

`submit_block_header` calls `check_target` → `check_pow` → `get_next_work_required` for every submitted header, regardless of whether it extends the mainchain or a fork. Inside `get_next_work_required`, the boundary block for the retarget window is fetched via:

```rust
// dogecoin.rs line 292-295
// TODO: check if it is correct to get block header by height from mainchain
// without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
```

`get_header_by_height` is implemented as:

```rust
// lib.rs lines 677-682
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
```

It always reads from `mainchain_height_to_header`, so it always returns the **mainchain** block at `height`, never the fork's ancestor.

The same pattern appears in `bitcoin.rs` and `litecoin.rs`:

```rust
// bitcoin.rs line 81
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
// litecoin.rs line 88
let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
```

**Dogecoin / Digishield is the worst case.** With `difficulty_adjustment_interval = 1` and `blocks_to_go_back = 0`, `height_first` equals `prev_block_header.block_height` — the same height as the fork's own previous block. The contract therefore computes the retarget timespan as:

```
actual_timespan = fork_prev_block.time − mainchain_block_at_same_height.time
```

instead of the correct:

```
actual_timespan = fork_prev_block.time − fork_prev_prev_block.time
```

An attacker who sets the fork's previous block timestamp later than the mainchain block at the same height inflates `actual_timespan`, which — after Digishield modulation — increases the target (lowers difficulty) for the next fork block. The Digishield clamp allows up to a 50 % difficulty reduction per step (`max_timespan = retarget_timespan + retarget_timespan/2`). The contract then enforces `expected_bits == block_header.bits` using this wrong value, so it accepts a fork block that the real Dogecoin network would reject.

For **Bitcoin and Litecoin** the window is 2016 blocks, so the wrong ancestor is only selected when the fork diverges before `height_first`; the impact is the same in kind but requires a longer-running fork.

---

### Impact Explanation

**Impact: High.**

The contract's canonical-chain state (`mainchain_tip_blockhash`, `mainchain_height_to_header`, `mainchain_header_to_height`) can be driven to a chain that the real network considers invalid. Downstream consumers of `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` would then verify transactions against a fraudulent chain. The corrupted canonical mapping is a direct, concrete state delta: the wrong block hash is stored as the tip and the wrong height-to-hash entries are written into `mainchain_height_to_header`.

---

### Likelihood Explanation

**Likelihood: Medium.**

Any unprivileged NEAR account can call `submit_blocks` (the `#[trusted_relayer]` gate can be bypassed by accounts granted `UnrestrictedSubmitBlocks`, and the relayer staking mechanism is a separate layer). The attacker only needs to:
1. Build a fork that diverges from the current mainchain tip.
2. Set fork-block timestamps to exploit the wrong `first_block_time`.
3. Mine fork blocks at the artificially lowered difficulty.

No privileged key or social engineering is required. The `TODO` comment in the production code confirms the developers are aware the logic is incomplete.

---

### Recommendation

Replace the `get_header_by_height` call inside `get_next_work_required` with a backward traversal through `get_prev_header` starting from `prev_block_header`, walking back exactly `blocks_to_go_back` steps. This traversal follows the fork's own chain and is already used correctly in the MTP and min-difficulty-block loops (e.g., `dogecoin.rs` lines 263–272, `bitcoin.rs` lines 64–70). The result should be cached in the `headers_pool` since all fork ancestors must already be present for the fork to be submitted.

---

### Proof of Concept

**Setup (Dogecoin mainnet, Digishield):**

1. Contract is initialized with mainchain blocks at heights 0…N. Mainchain block at height N has timestamp `T_main`.
2. Attacker submits a fork block `F_N` at height N (diverging from the mainchain at height N−1). `F_N` has timestamp `T_fork = T_main + 3600` (1 hour later; within `MAX_FUTURE_BLOCK_TIME_LOCAL = 7200 s` and above MTP).
3. Contract calls `get_next_work_required` for the next fork block `F_{N+1}`:
   - `height_first = N` (Digishield, `blocks_to_go_back = 0`)
   - `get_header_by_height(N)` → returns mainchain block at height N with timestamp `T_main`
   - `actual_timespan = F_N.time − T_main = 3600`
   - Digishield clamps: `modulated_timespan = 60 + (3600 − 60)/8 = 60 + 442 = 502` → clamped to `max_timespan = 90`
   - `new_target = prev_target × 90 / 60 = prev_target × 1.5` → 50 % easier
4. Contract enforces `expected_bits == block_header.bits` using this inflated target. Attacker mines `F_{N+1}` at 50 % lower difficulty and submits it; the contract accepts it.
5. Attacker repeats, accumulating chainwork. Because each fork block contributes less chainwork (lower bits), the attacker needs more blocks, but the difficulty reduction makes each block faster to mine. Eventually the fork's total chainwork exceeds the mainchain's, triggering `reorg_chain` and corrupting the canonical chain.

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/dogecoin.rs (L291-295)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;
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

**File:** contract/src/lib.rs (L549-567)
```rust
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

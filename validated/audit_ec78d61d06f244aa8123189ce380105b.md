### Title
Fork Retarget Difficulty Computed from Mainchain Ancestor Instead of Fork Ancestor — (`contract/src/lib.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`, `contract/src/dogecoin.rs`)

---

### Summary

When a fork block falls on a retarget boundary, `get_next_work_required` calls `blocks_getter.get_header_by_height(first_block_height)` to obtain the retarget-interval start timestamp. The sole implementation of `get_header_by_height` reads exclusively from `mainchain_height_to_header`. Fork blocks are never written to that map. Therefore, if the fork diverged at or before `first_block_height`, the contract silently uses the mainchain block's timestamp at that height instead of the fork's own ancestor's timestamp, producing an incorrect `bits` value for the fork's retarget block.

---

### Finding Description

**Entry point:** `submit_blocks` → `submit_block_header` → `check_target` → `check_pow` → `get_next_work_required` → `get_header_by_height`.

**`get_header_by_height` reads only the mainchain map:** [1](#0-0) 

**`store_fork_header` writes only to `headers_pool`, never to `mainchain_height_to_header`:** [2](#0-1) 

**Bitcoin retarget path calls `get_header_by_height` for the interval-start timestamp:** [3](#0-2) 

**Litecoin retarget path has the identical call:** [4](#0-3) 

**Dogecoin retarget path has the same call, and the codebase itself flags the issue with a TODO:** [5](#0-4) 

The `BlocksGetter` trait exposes two methods: `get_prev_header` (walks `headers_pool` via `prev_block_hash`, fork-aware) and `get_header_by_height` (reads `mainchain_height_to_header`, mainchain-only). [6](#0-5) 

Zcash is **not** affected: its `zcash_get_next_work_required` uses only `get_prev_header` to walk the averaging window, so it always follows the fork's own chain. [7](#0-6) 

---

### Impact Explanation

**Invariant broken:** The required `bits` for a fork block at a retarget boundary must be derived from the fork's own chain history. The contract instead derives it from the mainchain block at `first_block_height`.

**Difficulty manipulation:** An attacker who mines a fork diverging before `first_block_height` can set the fork's ancestor at that height to carry a **later** timestamp than the corresponding mainchain block. The correct fork retarget would then produce a **harder** target. But the contract uses the mainchain's **earlier** timestamp, yielding a **larger** `actual_time_taken` and therefore an **easier** target (up to the 4× cap). The fork's retarget block is accepted with `bits` that are easier than the fork's own chain history would mandate.

Concretely for Bitcoin (interval = 2016):
```
first_block_height = prev_height - 2015

actual_time_taken = prev_block_time - first_block_time

// Code uses: mainchain block at first_block_height  (wrong)
// Should use: fork ancestor at first_block_height   (correct)
```

If `mainchain_first_time < fork_first_time`, then `actual_time_taken` is inflated, the computed target is easier, and the fork block is accepted with a `bits` value that the fork's own history does not justify.

**Scope match:** This is a consensus-rule enforcement bug in the difficulty adjustment path that admits headers invalid under the intended network rules — exactly the Critical scope defined.

---

### Likelihood Explanation

The attacker must mine a fork chain of at least 2016 blocks (Bitcoin/Litecoin) with valid PoW at the current difficulty before the retarget boundary. This is a substantial but not impossible computational requirement (it is the standard 51%-attack prerequisite). The difficulty reduction gained (up to 4×) provides a meaningful advantage once the fork reaches the retarget boundary. For Dogecoin post-145k (per-block retarget, `blocks_to_go_back = 1`), the fork only needs to diverge two blocks back, making the precondition trivially cheap to satisfy.

---

### Recommendation

Replace the `get_header_by_height` call in all three retarget paths with a backward walk via `get_prev_header` from `prev_block_header`, counting back `difficulty_adjustment_interval` steps. This is already the pattern used by Zcash and by the `get_median_time_past` utility, and it is fork-aware because it follows `prev_block_hash` links through `headers_pool` rather than the mainchain height index.

```rust
// Replace:
let interval_tail = blocks_getter.get_header_by_height(first_block_height);

// With: walk back through the fork's own chain
let mut cursor = prev_block_header.clone();
for _ in 0..blocks_to_go_back {
    cursor = blocks_getter.get_prev_header(&cursor.block_header);
}
let first_block_time = cursor.block_header.time;
```

---

### Proof of Concept

State-machine test outline (NEAR sandbox or unit test with `skip_pow_verification = true`):

1. Initialize the contract at height 0 with a genesis block whose timestamp is `T0`.
2. Submit mainchain blocks 1–2016; set the mainchain block at height 1 (`first_block_height` for the retarget at 2016) to timestamp `T_main_1 = T0 + 100`.
3. Submit fork blocks 1–2015 diverging at height 0; set the fork's block at height 1 to timestamp `T_fork_1 = T0 + 500` (later than mainchain).
4. Compute the **correct** fork retarget: `actual_time_taken = T_fork_2015 - T_fork_1`.
5. Compute the **wrong** retarget the contract will use: `actual_time_taken = T_fork_2015 - T_main_1` (larger, easier).
6. Submit fork block 2016 with `bits` matching the wrong (easier) retarget.
7. Assert the contract accepts it (`submit_blocks` succeeds).
8. Assert that the correct fork retarget would have produced a harder `bits` value, proving the accepted block violates the fork's own chain rules.

### Citations

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

**File:** contract/src/dogecoin.rs (L291-295)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;
```

**File:** contract/src/utils.rs (L3-7)
```rust
pub trait BlocksGetter {
    fn get_prev_header(&self, current_header: &LightHeader) -> ExtendedHeader;
    #[allow(unused)]
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader;
}
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

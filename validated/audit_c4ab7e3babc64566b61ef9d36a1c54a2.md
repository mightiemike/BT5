### Title
Wrong Ancestor Block Used for Difficulty Calculation During Fork Validation — (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`)

---

### Summary

When validating a fork block at a difficulty-adjustment boundary, all three chain modules (`bitcoin.rs`, `litecoin.rs`, `dogecoin.rs`) call `get_header_by_height(first_block_height)` to obtain the timestamp of the first block in the difficulty window. This helper always returns the **mainchain** block at that height, not the fork's actual ancestor. When the fork diverges before `first_block_height`, the two blocks have different timestamps, so the expected difficulty is computed from the wrong value — a direct analog to the TWAP-vs-slot0 bug class.

---

### Finding Description

The difficulty retarget logic in all three chain modules follows the same pattern:

**`contract/src/bitcoin.rs` (`get_next_work_required`, lines 78–86):**
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

`get_header_by_height` is implemented in `contract/src/lib.rs` (lines 677–682) as:
```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
```

This unconditionally reads from `mainchain_height_to_header`, which maps only **mainchain** heights to hashes. When a fork block is being validated and the fork diverged before `first_block_height`, the mainchain block at that height is a different block (with a different timestamp) than the fork's actual ancestor at the same height.

The Dogecoin module even contains an explicit developer acknowledgment of this problem at `contract/src/dogecoin.rs` line 291:
```rust
// TODO: check if it is correct to get block header by height from mainchain
// without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
```

---

### Impact Explanation

The `expected_bits` value returned by `get_next_work_required` is compared against `block_header.bits` in `check_pow`. If the wrong timestamp is used, `expected_bits` is wrong. An attacker who controls the fork's block timestamps can craft a fork block at height H whose timestamp is later than the mainchain's block at H. When the contract then validates the fork's block at H+2, it computes:

```
modulated_timespan = fork_block[H+1].time − mainchain_block[H].time
```

instead of the correct:

```
modulated_timespan = fork_block[H+1].time − fork_block[H].time
```

Because `mainchain_block[H].time < fork_block[H].time` (attacker-chosen), the computed timespan is artificially inflated, yielding a lower `expected_bits` (easier difficulty). The attacker then submits a fork block whose `bits` field matches this incorrect lower target, and whose PoW hash satisfies the easier target. The contract accepts the block as valid.

**Corrupted invariant:** The light client accepts a fork block whose actual PoW does not meet the difficulty that the fork's own chain history requires. This corrupts `headers_pool`, `mainchain_tip_blockhash`, and ultimately the results of `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` for any downstream consumer.

---

### Likelihood Explanation

**Dogecoin** is the highest-risk target. After block 145,000, `difficulty_adjustment_interval = 1`, meaning every block is a retarget block. The attacker only needs to submit **one** diverging fork block (at height H) with a manipulated timestamp before the attack block (at H+2). The required mining work is minimal — only two fork blocks with valid PoW at the existing difficulty.

For **Bitcoin** and **Litecoin** (`difficulty_adjustment_interval = 2016`), the attacker must submit 2016 fork blocks before reaching the adjustment boundary, requiring substantial mining power and making exploitation significantly harder.

The entry path is `submit_blocks` (public, `#[payable]`, `#[trusted_relayer]`). The `trusted_relayer` mechanism involves a staking/application process managed by `Role::RelayerManager`. If the staking is permissionless (any account can stake and become a relayer), the attack is reachable by any unprivileged NEAR caller. The `RelayerManager` role description ("reject applications") implies a gated but open application process, making a malicious relayer a realistic threat actor.

---

### Recommendation

Replace the `get_header_by_height` call in all three `get_next_work_required` functions with an ancestor walk that follows `prev_block_hash` links from `prev_block_header` backward through `headers_pool` until the target height is reached. This ensures the correct fork ancestor is used regardless of what the current mainchain contains at that height. The Dogecoin module's own TODO comment (`contract/src/dogecoin.rs` line 291) already identifies this as the correct fix direction.

---

### Proof of Concept

1. Deploy the contract (Dogecoin build, mainnet, `skip_pow_verification = false`).
2. Initialize with a genesis at height H₀ ≥ 145,001 and submit enough blocks to establish a mainchain tip at height H.
3. As a trusted relayer, submit a fork block at height H whose `time` field is set to `mainchain_block[H].time + Δ` (e.g., Δ = 3600 s), with valid PoW matching the mainchain's bits at H−1.
4. Submit a fork block at H+1 with any valid timestamp.
5. For the fork block at H+2, compute `expected_bits` using the contract's formula with `first_block_time = mainchain_block[H].time` (not `fork_block[H].time`). The inflated timespan yields a lower `expected_bits`.
6. Mine a fork block at H+2 with `bits = expected_bits` (easier target) and a valid PoW hash satisfying that easier target.
7. Submit the H+2 fork block. The contract's `check_pow` computes the same incorrect `expected_bits` and accepts the block, even though the block's PoW does not satisfy the difficulty the fork's own history requires.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contract/src/dogecoin.rs (L291-297)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;

    calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
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

**File:** contract/src/lib.rs (L677-682)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```

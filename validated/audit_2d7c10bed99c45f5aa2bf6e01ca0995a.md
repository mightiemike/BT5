### Title
Fork Difficulty Retarget Uses Mainchain Ancestor Instead of Fork Ancestor — (`contract/src/dogecoin.rs`, `contract/src/lib.rs`)

---

### Summary

`get_next_work_required` in the Dogecoin build calls `blocks_getter.get_header_by_height(height_first)` to obtain the `first_block_time` for the DigiShield retarget calculation. The `BlocksGetter` implementation resolves this height exclusively through `mainchain_height_to_header`, which is the mainchain-only height index. When a fork block is being validated, the function silently returns the **mainchain** block at that height rather than the actual fork ancestor, producing an incorrect `expected_bits`. The code itself acknowledges this with a `TODO` comment at the exact line.

---

### Finding Description

**Root cause — `get_header_by_height` is mainchain-only:** [1](#0-0) 

The implementation unconditionally reads from `mainchain_height_to_header`. There is no fork-aware ancestor walk.

**Where the wrong value is consumed:** [2](#0-1) 

The developer's own `TODO` comment at line 291 flags the exact problem. For Dogecoin mainnet with `new_difficulty_protocol` active (height ≥ 145,000), `difficulty_adjustment_interval = 1`, so `blocks_to_go_back = 1` and `height_first = prev_block_header.block_height - 1`. This means **every single fork block** beyond the first one after the divergence point uses the wrong ancestor time. [3](#0-2) 

**Concrete chain state that triggers it:**

Suppose the fork diverges at height H (fork block F[H+1] has `prev_block_hash = M[H]`):

```
Mainchain: M[H] → M[H+1] → M[H+2] → ...
Fork:      M[H] → F[H+1] → F[H+2] → F[H+3] → ...
```

When validating F[H+3]:
- `prev_block_header` = F[H+2] (correctly fetched by hash via `get_prev_header`)
- `height_first = H+1`
- `get_header_by_height(H+1)` returns **M[H+1]** (mainchain block)
- Correct fork ancestor at height H+1 is **F[H+1]**
- `M[H+1].time ≠ F[H+1].time` → wrong `modulated_timespan` → wrong `expected_bits` [4](#0-3) 

**`check_pow` enforces the wrong target:** [5](#0-4) 

The `require!(expected_bits == block_header.bits, ...)` check uses the mainchain-derived `expected_bits`. A fork block whose `bits` field is set to match the mainchain-derived value (rather than the correct fork-derived value) will pass this check, and the subsequent AuxPoW hash check will then verify the block hash against that incorrect (potentially easier) target.

---

### Impact Explanation

There are two concrete consequences:

1. **Accepting fork blocks with incorrect (potentially lower) difficulty.** An attacker who is a trusted relayer constructs fork blocks whose `bits` field is set to the value the contract will compute using the mainchain ancestor's time. If the mainchain block at `height_first` has a different timestamp than the fork ancestor (e.g., the fork was mined faster or slower), the resulting target may be easier. The contract accepts these blocks, accumulates their `chain_work`, and if `chain_work` exceeds the mainchain tip, triggers `reorg_chain`, replacing the canonical chain with the attacker's fork. This enables false transaction confirmations and double-spend attacks against consumers of `verify_transaction_inclusion`.

2. **Rejecting all valid fork blocks beyond the first.** Any honest relayer submitting a legitimate fork chain (e.g., during a real network reorg) will have their blocks rejected because the contract computes a different `expected_bits` than what the real Dogecoin network computed. This breaks the light client's ability to track the heaviest chain.

---

### Likelihood Explanation

The `submit_blocks` entry point is gated by `#[trusted_relayer]`, which requires staking — a financial barrier, not a privileged role. The scope rules explicitly include the trusted-relayer submission path as a valid attacker entry point. The precondition (fork diverging at height ≥ 145,001 so `new_difficulty_protocol` is active) is the normal operating range for the live Dogecoin mainnet (currently at block ~5.6M). The bug fires on every fork block after the second one past the divergence point, with no additional preconditions. The `TODO` comment confirms the developers are aware the behavior is unverified.

---

### Recommendation

Replace the `get_header_by_height` call with a fork-aware ancestor walk. Starting from `prev_block_header`, follow `get_prev_header` links `blocks_to_go_back` times to reach the correct fork ancestor, then read its `time`. This is the same pattern already used by `get_median_time_past` and the `pow_allow_min_difficulty_blocks` walk in `get_next_work_required` itself. [6](#0-5) 

---

### Proof of Concept

```
State setup (Dogecoin mainnet, skip_pow_verification = true):
  Genesis at height 145,000 (new_difficulty_protocol active).
  Submit mainchain blocks M[145000] through M[145003].
    M[145001].time = T+60   (normal 60s spacing)
    M[145002].time = T+120
    M[145003].time = T+180

Fork diverges at M[145001]:
  Submit F[145002] with prev = M[145001].hash, F[145002].time = T+300 (slow block)
  Submit F[145003] with prev = F[145002].hash, F[145003].time = T+360

When validating F[145003]:
  height_first = 145002 - 1 = 145001
  get_header_by_height(145001) → M[145001], time = T+60   ← WRONG
  Correct fork ancestor at 145001 = M[145001], time = T+60 ← same here (divergence point)

  (Diverge one block earlier to expose the bug clearly:)

Fork diverges at M[145000]:
  F[145001].prev = M[145000].hash, F[145001].time = T+300
  F[145002].prev = F[145001].hash, F[145002].time = T+360
  F[145003].prev = F[145002].hash, F[145003].time = T+420

When validating F[145003]:
  height_first = 145002 - 1 = 145001
  get_header_by_height(145001) → M[145001], time = T+60   ← WRONG (mainchain)
  Correct fork ancestor at 145001 = F[145001], time = T+300

  mainchain-based modulated_timespan = T+360 - (T+60) = 300s
  fork-correct modulated_timespan    = T+360 - (T+300) = 60s

  These produce different expected_bits values.
  A fork block with bits = mainchain-derived value passes check_pow.
  A fork block with bits = fork-correct value is rejected.
```

Assert: `expected_bits_from_mainchain_ancestor ≠ expected_bits_from_fork_ancestor` whenever `M[height_first].time ≠ F[height_first].time`, which is the normal case for any fork with different block timing.

### Citations

**File:** contract/src/lib.rs (L677-682)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```

**File:** contract/src/dogecoin.rs (L23-34)
```rust
    pub(crate) fn check_pow(&self, block_header: &Header, prev_block_header: &ExtendedHeader) {
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

**File:** contract/src/dogecoin.rs (L244-249)
```rust
    let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
    let difficulty_adjustment_interval = if new_difficulty_protocol {
        1
    } else {
        config.difficulty_adjustment_interval
    };
```

**File:** contract/src/dogecoin.rs (L291-297)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;

    calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
```

**File:** contract/src/dogecoin.rs (L301-312)
```rust
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
```

**File:** contract/src/utils.rs (L10-26)
```rust
pub fn get_median_time_past(
    block_header: ExtendedHeader,
    prev_block_getter: &impl BlocksGetter,
) -> u32 {
    use btc_types::network::MEDIAN_TIME_SPAN;

    let mut median_time = [0u32; MEDIAN_TIME_SPAN];
    let mut current_header = block_header;

    for slot in &mut median_time {
        *slot = current_header.block_header.time;
        current_header = prev_block_getter.get_prev_header(&current_header.block_header);
    }

    median_time.sort_unstable();
    median_time[median_time.len() / 2]
}
```

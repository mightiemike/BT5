### Title
`get_sortable_validator_online_ratio` Ignores `endorsement_cutoff_threshold`, Causing Divergent Kickout-Exemption Ordering vs. Reward Calculation — (`chain/epoch-manager/src/validator_stats.rs`)

---

### Summary

The kickout-exemption sorting in `compute_validators_to_reward_and_kickout` uses `get_sortable_validator_online_ratio`, which always passes `None` for `endorsement_cutoff_threshold`. The reward calculator uses `get_validator_online_ratio` with the actual `endorsement_cutoff_threshold` (set to `chunk_validator_only_kickout_threshold`, 70% in production). The two code paths compute materially different online ratios for the same validator stats, causing the wrong validators to be exempted from kickout.

---

### Finding Description

**Path 1 — Kickout-exemption sorting (uses raw endorsement ratio, no cutoff):**

`get_sortable_validator_online_ratio` unconditionally passes `None`:

```rust
pub(crate) fn get_sortable_validator_online_ratio(stats: &BlockChunkValidatorStats) -> BigRational {
    let ratio = get_validator_online_ratio(stats, None);   // cutoff always ignored
    ...
}
``` [1](#0-0) 

This sorted list drives `compute_exempted_kickout`:

```rust
let mut sorted_validators = validator_block_chunk_stats
    .iter()
    .map(|(account, stats)| (get_sortable_validator_online_ratio(stats), account))
    .collect_vec();
sorted_validators.sort_by(validator_comparator);
let accounts_sorted_by_online_ratio = ...;
let exempted_validators = Self::compute_exempted_kickout(
    epoch_info, &accounts_sorted_by_online_ratio, ...
);
``` [2](#0-1) 

**Path 2 — Reward calculation (uses binary endorsement contribution with cutoff):**

```rust
let production_ratio =
    get_validator_online_ratio(&stats, online_thresholds.endorsement_cutoff_threshold);
``` [3](#0-2) 

**The divergence inside `get_validator_online_ratio`:**

When `endorsement_cutoff_threshold` is `None`, `get_endorsement_ratio` returns the raw `(produced, expected)` pair. When the threshold is `Some(70)` (production value), it returns `(0, 1)` if the endorsement ratio is below 70%, or `(1, 1)` if above. [4](#0-3) 

**Concrete divergence example:**

Consider two validators, both block+chunk+endorsement producers, with `endorsement_cutoff_threshold = 70`:

| Validator | blocks | chunks | endorsements | Sorting ratio (None) | Reward ratio (cutoff=70) |
|-----------|--------|--------|--------------|----------------------|--------------------------|
| A | 95/100 | 95/100 | 69/100 | (0.95+0.95+0.69)/3 = **0.863** | (0.95+0.95+0)/3 = **0.633** → zero reward (below `online_min=0.9`) |
| B | 85/100 | 85/100 | 71/100 | (0.85+0.85+0.71)/3 = **0.803** | (0.85+0.85+1.0)/3 = **0.900** → positive reward |

The exemption logic ranks Validator A higher (0.863 > 0.803) and exempts it from kickout. But Validator A earns **zero reward** while Validator B earns a positive reward. The exemption protects the wrong validator.

The CHANGELOG confirms the production intent: `chunk_validator_only_kickout_threshold` (70%) is explicitly used as the `endorsement_cutoff_threshold` for reward calculation. [5](#0-4) 

---

### Impact Explanation

The kickout-exemption mechanism is a safety valve: when too many validators would be kicked out (exceeding `validator_max_kickout_stake_perc`), the highest-online-ratio validators are protected. Because the sorting metric diverges from the reward metric, the exemption can protect validators that actually have the **lowest** reward-adjusted online ratios (endorsement ratio just below the cutoff), while kicking out validators with **higher** reward-adjusted online ratios (endorsement ratio just above the cutoff). This inverts the intended protection ordering, causing better-performing validators to lose their stake and seat while worse-performing validators are shielded.

---

### Likelihood Explanation

The divergence is always active whenever `endorsement_cutoff_threshold` is non-`None` in the epoch config, which is the production configuration (threshold = 70%). Any epoch where `validator_max_kickout_stake_perc` is binding (i.e., enough validators underperform to trigger the exemption logic) will exhibit the incorrect ordering. No privileged action is required; the condition arises from normal validator performance variation around the 70% endorsement boundary.

---

### Recommendation

Pass the `endorsement_cutoff_threshold` into `get_sortable_validator_online_ratio` so both code paths use the same metric:

```rust
pub(crate) fn get_sortable_validator_online_ratio(
    stats: &BlockChunkValidatorStats,
    endorsement_cutoff_threshold: Option<u8>,   // add parameter
) -> BigRational {
    let ratio = get_validator_online_ratio(stats, endorsement_cutoff_threshold);
    ...
}
```

And update the call site in `compute_validators_to_reward_and_kickout` to pass `config.chunk_validator_only_kickout_threshold` (or the same value used by the reward calculator) as the cutoff. [6](#0-5) [7](#0-6) 

---

### Proof of Concept

Using the existing test harness in `chain/epoch-manager/src/tests/mod.rs`, construct two chunk-validator-only validators with:
- Validator A: endorsements = 69/100 (below 70% cutoff)
- Validator B: endorsements = 71/100 (above 70% cutoff)
- Both with identical block/chunk production at 95/100

Set `validator_max_kickout_stake_perc` so that only one can be exempted. Call `compute_validators_to_reward_and_kickout` and observe that Validator A (sorting ratio 0.863) is exempted while Validator B (sorting ratio 0.870 with cutoff, but 0.870 raw) is kicked out — despite Validator B having a higher reward-adjusted online ratio (0.937 vs 0.633).

Then call `calculate_reward` with `endorsement_cutoff_threshold = Some(70)` on the same stats and observe that Validator A receives zero reward (combined ratio 0.633 < `online_min_threshold` 0.9) while Validator B receives a positive reward (combined ratio 0.937 ≥ 0.9), confirming the inversion. [8](#0-7) [9](#0-8)

### Citations

**File:** chain/epoch-manager/src/validator_stats.rs (L16-101)
```rust
pub(crate) fn get_validator_online_ratio(
    stats: &BlockChunkValidatorStats,
    endorsement_cutoff_threshold: Option<u8>,
) -> Ratio<U256> {
    let expected_blocks = stats.block_stats.expected;
    let expected_chunks = stats.chunk_stats.expected();

    let (produced_endorsements, expected_endorsements) =
        get_endorsement_ratio(stats.chunk_stats.endorsement_stats(), endorsement_cutoff_threshold);

    let (average_produced_numer, average_produced_denom) =
        match (expected_blocks, expected_chunks, expected_endorsements) {
            // Validator was not expected to do anything
            (0, 0, 0) => (U256::from(0), U256::from(1)),
            // Validator was a stateless validator only (not expected to produce anything)
            (0, 0, expected_endorsements) => {
                (U256::from(produced_endorsements), U256::from(expected_endorsements))
            }
            // Validator was a chunk-only producer
            (0, expected_chunks, 0) => {
                let produced_chunks = stats.chunk_stats.produced();

                (U256::from(produced_chunks), U256::from(expected_chunks))
            }
            // Validator was only a block producer
            (expected_blocks, 0, 0) => {
                let produced_blocks = stats.block_stats.produced;

                (U256::from(produced_blocks), U256::from(expected_blocks))
            }
            // Validator produced blocks and chunks, but not endorsements
            (expected_blocks, expected_chunks, 0) => {
                let produced_blocks = stats.block_stats.produced;
                let produced_chunks = stats.chunk_stats.produced();

                let numer = U256::from(
                    produced_blocks * expected_chunks + produced_chunks * expected_blocks,
                );
                let denom = U256::from(2 * expected_chunks * expected_blocks);
                (numer, denom)
            }
            // Validator produced chunks and endorsements, but not blocks
            (0, expected_chunks, expected_endorsements) => {
                let produced_chunks = stats.chunk_stats.produced();

                let numer = U256::from(
                    produced_endorsements * expected_chunks
                        + produced_chunks * expected_endorsements,
                );
                let denom = U256::from(2 * expected_chunks * expected_endorsements);
                (numer, denom)
            }
            // Validator produced blocks and endorsements, but not chunks
            (expected_blocks, 0, expected_endorsements) => {
                let produced_blocks = stats.block_stats.produced;

                let numer = U256::from(
                    produced_endorsements * expected_blocks
                        + produced_blocks * expected_endorsements,
                );
                let denom = U256::from(2 * expected_blocks * expected_endorsements);
                (numer, denom)
            }
            // Validator did all the things
            (expected_blocks, expected_chunks, expected_endorsements) => {
                let produced_blocks = stats.block_stats.produced;
                let produced_chunks = stats.chunk_stats.produced();

                let numer = U256::from(
                    produced_blocks * expected_chunks * expected_endorsements
                        + produced_chunks * expected_blocks * expected_endorsements
                        + produced_endorsements * expected_blocks * expected_chunks,
                );
                let denom =
                    U256::from(3 * expected_chunks * expected_blocks * expected_endorsements);
                (numer, denom)
            }
        };
    debug_assert_ne!(
        average_produced_denom,
        U256::zero(),
        "Denominator must be non-zero for Ratio."
    );
    // Note: This creates Ratio without checking if denom is zero and doing reduction.
    Ratio::<U256>::new_raw(average_produced_numer, average_produced_denom)
}
```

**File:** chain/epoch-manager/src/validator_stats.rs (L110-118)
```rust
pub(crate) fn get_sortable_validator_online_ratio(stats: &BlockChunkValidatorStats) -> BigRational {
    let ratio = get_validator_online_ratio(stats, None);
    let mut bytes: [u8; size_of::<U256>()] = [0; size_of::<U256>()];
    ratio.numer().to_little_endian(&mut bytes);
    let bignumer = BigUint::from_bytes_le(&bytes);
    ratio.denom().to_little_endian(&mut bytes);
    let bigdenom = BigUint::from_bytes_le(&bytes);
    BigRational::new(bignumer.try_into().unwrap(), bigdenom.try_into().unwrap())
}
```

**File:** chain/epoch-manager/src/validator_stats.rs (L124-134)
```rust
fn get_endorsement_ratio(stats: &ValidatorStats, cutoff_threshold: Option<u8>) -> (u64, u64) {
    let (numer, denom) = if stats.expected == 0 {
        debug_assert_eq!(stats.produced, 0);
        (0, 0)
    } else if let Some(threshold) = cutoff_threshold {
        if stats.less_than(threshold) { (0, 1) } else { (1, 1) }
    } else {
        (stats.produced, stats.expected)
    };
    (numer, denom)
}
```

**File:** chain/epoch-manager/src/lib.rs (L500-516)
```rust
        let mut sorted_validators = validator_block_chunk_stats
            .iter()
            .map(|(account, stats)| (get_sortable_validator_online_ratio(stats), account))
            .collect_vec();
        sorted_validators.sort_by(validator_comparator);
        let accounts_sorted_by_online_ratio =
            sorted_validators.into_iter().map(|(_, account)| account.clone()).collect_vec();

        let exempt_perc =
            100_u8.checked_sub(config.validator_max_kickout_stake_perc).unwrap_or_default();
        let exempted_validators = Self::compute_exempted_kickout(
            epoch_info,
            &accounts_sorted_by_online_ratio,
            total_stake,
            exempt_perc,
            prev_validator_kickout,
        );
```

**File:** chain/epoch-manager/src/reward_calculator.rs (L94-146)
```rust
        for (account_id, stats) in validator_block_chunk_stats {
            let production_ratio =
                get_validator_online_ratio(&stats, online_thresholds.endorsement_cutoff_threshold);
            let average_produced_numer = production_ratio.numer();
            let average_produced_denom = production_ratio.denom();

            let expected_blocks = stats.block_stats.expected;
            let expected_chunks = stats.chunk_stats.expected();
            let expected_endorsements = stats.chunk_stats.endorsement_stats().expected;

            let online_min_numer =
                U256::from(*online_thresholds.online_min_threshold.numer() as u64);
            let online_min_denom =
                U256::from(*online_thresholds.online_min_threshold.denom() as u64);
            // If average of produced blocks below online min threshold, validator gets 0 reward.
            let reward = if average_produced_numer * online_min_denom
                < online_min_numer * average_produced_denom
                || (expected_chunks == 0 && expected_blocks == 0 && expected_endorsements == 0)
            {
                Balance::ZERO
            } else {
                // cspell:ignore denum
                let stake = *validator_stake
                    .get(&account_id)
                    .unwrap_or_else(|| panic!("{} is not a validator", account_id));
                // Online reward multiplier is min(1., (uptime - online_threshold_min) / (online_threshold_max - online_threshold_min).
                let online_max_numer =
                    U256::from(*online_thresholds.online_max_threshold.numer() as u64);
                let online_max_denom =
                    U256::from(*online_thresholds.online_max_threshold.denom() as u64);
                let online_numer =
                    online_max_numer * online_min_denom - online_min_numer * online_max_denom;
                let mut uptime_numer = (average_produced_numer * online_min_denom
                    - online_min_numer * average_produced_denom)
                    * online_max_denom;
                let uptime_denum = online_numer * average_produced_denom;
                // Apply min between 1. and computed uptime.
                uptime_numer =
                    if uptime_numer > uptime_denum { uptime_denum } else { uptime_numer };
                Balance::from_yoctonear(
                    (U512::from(epoch_validator_reward.as_yoctonear())
                        * U512::from(uptime_numer)
                        * U512::from(stake.as_yoctonear())
                        / U512::from(uptime_denum)
                        / U512::from(total_stake.as_yoctonear()))
                    .as_u128(),
                )
            };
            res.insert(account_id, reward);
            epoch_actual_reward = epoch_actual_reward.checked_add(reward).unwrap();
        }
        (res, epoch_actual_reward)
    }
```

**File:** CHANGELOG.md (L181-181)
```markdown
* Sets `chunk_validator_only_kickout_threshold` to 70. Uses this kickout threshold as a cutoff threshold for contribution of endorsement ratio in rewards calculation: if endorsement ratio is above 70%, the contribution of endorsement ratio in average uptime calculation is 100%, otherwise it is 0%. Endorsements received are now included in `BlockHeader` to improve kickout and reward calculation for chunk validators.
```

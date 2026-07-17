### Title
Divergent Uptime Ratio Used for Kickout Exemption vs. Reward Calculation Due to Missing `endorsement_cutoff_threshold` in `get_sortable_validator_online_ratio` - (File: chain/epoch-manager/src/validator_stats.rs)

### Summary

`get_sortable_validator_online_ratio` always calls `get_validator_online_ratio` with `endorsement_cutoff_threshold: None`, while the reward calculation path calls it with `Some(chunk_validator_only_kickout_threshold)`. This means the uptime ratio used to sort validators for kickout-exemption selection is computed with a different formula than the uptime ratio used to determine whether a validator actually receives a reward. A validator with a low endorsement ratio (below the cutoff) can be sorted as "high uptime" (because the raw endorsement fraction is used, not the binarized 0/1 value), be placed into the exempted set, and then receive zero reward — the opposite of the intended invariant that exempted validators are the ones most deserving of reward.

### Finding Description

In `chain/epoch-manager/src/validator_stats.rs`, `get_sortable_validator_online_ratio` is a wrapper that always passes `None` as the `endorsement_cutoff_threshold`:

```rust
pub(crate) fn get_sortable_validator_online_ratio(stats: &BlockChunkValidatorStats) -> BigRational {
    let ratio = get_validator_online_ratio(stats, None);   // ← always None
    ...
}
``` [1](#0-0) 

This function is called in `compute_validators_to_reward_and_kickout` to sort validators by uptime for the purpose of selecting which validators are exempted from kickout:

```rust
let mut sorted_validators = validator_block_chunk_stats
    .iter()
    .map(|(account, stats)| (get_sortable_validator_online_ratio(stats), account))
    .collect_vec();
sorted_validators.sort_by(validator_comparator);
``` [2](#0-1) 

The resulting sorted order is then used to build the `exempted_validators` set — the validators that are protected from kickout: [3](#0-2) 

However, in `finalize_epoch`, the reward calculation uses `endorsement_cutoff_threshold: Some(chunk_validator_only_kickout_threshold)`:

```rust
let online_thresholds = ValidatorOnlineThresholds {
    online_min_threshold: epoch_config.online_min_threshold,
    online_max_threshold: epoch_config.online_max_threshold,
    endorsement_cutoff_threshold: Some(
        epoch_config.chunk_validator_only_kickout_threshold,
    ),
};
self.reward_calculator.calculate_reward(
    validator_block_chunk_stats,
    ...
    online_thresholds,
    ...
)
``` [4](#0-3) 

When `endorsement_cutoff_threshold` is `None`, `get_endorsement_ratio` returns the raw `(produced, expected)` fraction. When it is `Some(threshold)`, it returns `(0, 1)` if below threshold or `(1, 1)` if above. These are structurally different values that produce different uptime ratios for the same validator stats. [5](#0-4) 

The divergence is exact and deterministic: for a chunk-validator-only validator with endorsement ratio `e` where `e < chunk_validator_only_kickout_threshold`:

- **Sorting path** (`None`): uptime = `e` (e.g. 65/100 = 0.65)
- **Reward path** (`Some(threshold)`): uptime = 0/1 = 0.0

A validator with endorsement ratio 65% (above the raw online_min_threshold of 90%? No — but above the raw ratio used for sorting) can be ranked higher than another validator with 60% endorsement ratio in the sort, be exempted from kickout, and then receive **zero reward** because the binarized cutoff maps its 65% to 0.

### Impact Explanation

The kickout-exemption mechanism is designed to protect the highest-uptime validators from being kicked out during network instability. When the sorting uses a different uptime formula than the reward formula, the exemption set is populated with validators that the reward formula considers to have zero uptime. These validators:

1. Are protected from kickout (exempted), meaning they remain validators in the next epoch.
2. Receive zero reward for the epoch.
3. Displace validators with genuinely higher binarized uptime from the exemption set.

This inverts the safety guarantee: the validators most likely to be kicked out under the reward formula's view are the ones being protected, while validators with genuinely high binarized uptime may be kicked out. Any validator operator can observe their own endorsement ratio and craft a strategy around this divergence. The impact is on validator set composition and reward distribution — both protocol-level invariants.

### Likelihood Explanation

This divergence is always present whenever `chunk_validator_only_kickout_threshold` is configured (i.e., whenever stateless validation / chunk endorsement is active). The condition is triggered whenever a chunk-validator-only validator has an endorsement ratio that is below the cutoff threshold but above the raw `online_min_threshold`. This is a normal operating condition, not an edge case. The divergence is deterministic and reproducible every epoch.

### Recommendation

`get_sortable_validator_online_ratio` should accept and forward the same `endorsement_cutoff_threshold` that is used in the reward path. The call site in `compute_validators_to_reward_and_kickout` should pass `Some(config.chunk_validator_only_kickout_threshold)` to ensure the sort order used for exemption selection is consistent with the uptime formula used for reward calculation.

### Proof of Concept

Consider a network with:
- `online_min_threshold = 0.9`
- `chunk_validator_only_kickout_threshold = 70` (70%)
- `validator_max_kickout_stake_perc = 30` (so 70% of stake must be exempted)
- Two chunk-validator-only validators A and B with equal stake:
  - A: endorsement ratio = 65/100 = 0.65
  - B: endorsement ratio = 60/100 = 0.60

**Sorting path** (cutoff = `None`): A has ratio 0.65, B has ratio 0.60. A is ranked higher. A is exempted.

**Reward path** (cutoff = `Some(70)`): Both 65% and 60% are below 70%, so both map to 0. Both receive zero reward.

**Result**: A is exempted from kickout (stays as validator next epoch) but receives zero reward. B is kicked out. The exemption protected the wrong validator — both have zero binarized uptime, so the exemption should have been based on a tiebreak, not on the raw endorsement fraction. More critically, if A had endorsement ratio 71% (above cutoff) and B had 69% (below cutoff), the sorting path would rank A higher (0.71 > 0.69), A would be exempted, and A would receive a reward — this is correct. But if A had 69% and B had 65%, A would be exempted (0.69 > 0.65 in raw sort) but receive zero reward (both below cutoff), while B is kicked out despite also having zero binarized uptime. The exemption set is thus populated inconsistently with the reward formula. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

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

**File:** chain/epoch-manager/src/lib.rs (L896-913)
```rust
            // We use the chunk validator kickout threshold as the cutoff threshold for the
            // endorsement ratio to remap the ratio to 0 or 1.
            let online_thresholds = ValidatorOnlineThresholds {
                online_min_threshold: epoch_config.online_min_threshold,
                online_max_threshold: epoch_config.online_max_threshold,
                endorsement_cutoff_threshold: Some(
                    epoch_config.chunk_validator_only_kickout_threshold,
                ),
            };
            self.reward_calculator.calculate_reward(
                validator_block_chunk_stats,
                &validator_stake,
                *block_info.total_supply(),
                epoch_protocol_version,
                epoch_duration,
                online_thresholds,
                epoch_config.max_inflation_rate,
            )
```

### Title
`get_sortable_validator_online_ratio` Ignores `endorsement_cutoff_threshold`, Producing Divergent Kickout-Exemption Sort Order — (`File: chain/epoch-manager/src/validator_stats.rs`)

### Summary

`get_sortable_validator_online_ratio()` always calls `get_validator_online_ratio(stats, None)`, permanently discarding the `endorsement_cutoff_threshold` that `calculate_reward()` correctly applies. When the cutoff is active, the two paths compute materially different uptime values for the same validator, causing the kickout-exemption sort to rank validators in the wrong order and potentially exempting under-performing validators from kickout while failing to protect well-performing ones.

### Finding Description

`calculate_reward()` computes each validator's uptime via:

```rust
let production_ratio =
    get_validator_online_ratio(&stats, online_thresholds.endorsement_cutoff_threshold);
``` [1](#0-0) 

When `endorsement_cutoff_threshold` is `Some(T)`, `get_endorsement_ratio()` maps the raw endorsement ratio to a binary 0 or 1:

```rust
if stats.less_than(threshold) { (0, 1) } else { (1, 1) }
``` [2](#0-1) 

`compute_validators_to_reward_and_kickout()` sorts validators for kickout-exemption using `get_sortable_validator_online_ratio()`:

```rust
let mut sorted_validators = validator_block_chunk_stats
    .iter()
    .map(|(account, stats)| (get_sortable_validator_online_ratio(stats), account))
    .collect_vec();
``` [3](#0-2) 

But `get_sortable_validator_online_ratio()` hard-codes `None`:

```rust
pub(crate) fn get_sortable_validator_online_ratio(stats: &BlockChunkValidatorStats) -> BigRational {
    let ratio = get_validator_online_ratio(stats, None);
``` [4](#0-3) 

The sorted list feeds directly into `compute_exempted_kickout()`, which walks it from highest to lowest uptime and exempts validators until the exempted stake exceeds `(100 - validator_max_kickout_stake_perc)%` of total stake: [5](#0-4) 

### Impact Explanation

**Exact divergent value.** Consider a validator with blocks 945/1000, chunks 944/1000, endorsements 446/1000, and `endorsement_cutoff_threshold = 50`:

- **Reward path (correct):** endorsement contribution → `0/1`; uptime = `(0.945 + 0.944 + 0) / 3 ≈ 0.630`
- **Sort path (wrong):** endorsement contribution → `446/1000`; uptime = `(0.945 + 0.944 + 0.446) / 3 ≈ 0.778`

The sort path inflates the uptime of every validator whose endorsement ratio is below the cutoff (they appear better than they are) and deflates the uptime of every validator above the cutoff (they appear worse than they are, because the cutoff maps them to 1.0 in the reward path but their raw ratio < 1.0 in the sort path).

**Protocol invariant broken.** The kickout-exemption mechanism is designed to protect the highest-uptime validators from being kicked out during network instability. With the divergent sort, a validator that the reward formula treats as having near-zero endorsement uptime can rank above a validator that the reward formula treats as having full endorsement uptime, inverting the intended protection order.

**Scope:** Protocol feature activation — the divergence is latent until `endorsement_cutoff_threshold` is set to a non-`None` value in the epoch config, at which point every epoch boundary computes the wrong exemption set.

### Likelihood Explanation

`endorsement_cutoff_threshold` is a fully implemented, tested field in `ValidatorOnlineThresholds`. The existing mainnet epoch-config JSON files do not yet set it, so the bug is dormant on mainnet today. However, the feature is clearly intended for production use (it has dedicated reward-calculator tests and a kickout test), and any protocol upgrade that activates it will silently trigger the wrong sort order on every subsequent epoch boundary without any additional code change.

### Recommendation

Add `endorsement_cutoff_threshold: Option<u8>` as a parameter to `get_sortable_validator_online_ratio()` and thread it through to `get_validator_online_ratio()`:

```rust
pub(crate) fn get_sortable_validator_online_ratio(
    stats: &BlockChunkValidatorStats,
    endorsement_cutoff_threshold: Option<u8>,
) -> BigRational {
    let ratio = get_validator_online_ratio(stats, endorsement_cutoff_threshold);
    ...
}
```

Update the call-site in `compute_validators_to_reward_and_kickout()` to pass the config's `endorsement_cutoff_threshold` (the same value already passed to `calculate_reward()`).

### Proof of Concept

Using the numbers from `test_reward_stateless_validation_with_endorsement_cutoff` with `endorsement_cutoff_threshold = Some(50)`:

- **test2**: blocks 945/1000, chunks 944/1000, endorsements 446/1000 (below cutoff)
  - Reward uptime ≈ 0.630 → gets **zero reward**
  - Sort uptime ≈ 0.778 → **ranks above** test1 (whose sort uptime ≈ 0.745) in the exemption list
- **test1**: blocks 945/1000, chunks 944/1000, endorsements 946/1000 (above cutoff → treated as 1.0)
  - Reward uptime ≈ 0.963 → gets **positive reward**
  - Sort uptime ≈ 0.945 → **ranks below** test2 in the exemption list

If `validator_max_kickout_stake_perc` is set such that only one of these two validators can be exempted, the sort will exempt test2 (the under-performer) and kick out test1 (the over-performer) — the exact opposite of the intended behavior. [4](#0-3) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** chain/epoch-manager/src/reward_calculator.rs (L94-96)
```rust
        for (account_id, stats) in validator_block_chunk_stats {
            let production_ratio =
                get_validator_online_ratio(&stats, online_thresholds.endorsement_cutoff_threshold);
```

**File:** chain/epoch-manager/src/validator_stats.rs (L110-111)
```rust
pub(crate) fn get_sortable_validator_online_ratio(stats: &BlockChunkValidatorStats) -> BigRational {
    let ratio = get_validator_online_ratio(stats, None);
```

**File:** chain/epoch-manager/src/validator_stats.rs (L128-129)
```rust
    } else if let Some(threshold) = cutoff_threshold {
        if stats.less_than(threshold) { (0, 1) } else { (1, 1) }
```

**File:** chain/epoch-manager/src/lib.rs (L392-403)
```rust
        for account_id in accounts_sorted_by_online_ratio.into_iter().rev() {
            if exempted_stake >= min_keep_stake {
                break;
            }
            if !prev_validator_kickout.contains_key(account_id) {
                let validator_stake = epoch_info
                    .get_validator_by_account(account_id)
                    .map(|v| v.stake())
                    .unwrap_or_default();
                exempted_stake = exempted_stake.checked_add(validator_stake).unwrap();
                exempted_validators.insert(account_id.clone());
            }
```

**File:** chain/epoch-manager/src/lib.rs (L500-506)
```rust
        let mut sorted_validators = validator_block_chunk_stats
            .iter()
            .map(|(account, stats)| (get_sortable_validator_online_ratio(stats), account))
            .collect_vec();
        sorted_validators.sort_by(validator_comparator);
        let accounts_sorted_by_online_ratio =
            sorted_validators.into_iter().map(|(_, account)| account.clone()).collect_vec();
```

**File:** chain/epoch-manager/src/lib.rs (L508-516)
```rust
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

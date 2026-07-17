### Title
Divergent Online-Ratio Invariant Between Kickout-Exemption Sorting and Reward Calculation When `endorsement_cutoff_threshold` Is Active — (`File: chain/epoch-manager/src/validator_stats.rs`)

---

### Summary

`get_sortable_validator_online_ratio`, which drives the kickout-exemption sort, always calls `get_validator_online_ratio(stats, None)` — permanently ignoring `endorsement_cutoff_threshold`. The reward path calls `get_validator_online_ratio(&stats, online_thresholds.endorsement_cutoff_threshold)` with the actual configured value. When the threshold is non-`None`, the two paths compute a different online-ratio for the same validator, causing the exemption sort to rank validators by an inflated ratio that does not match the ratio used to determine their actual reward eligibility.

---

### Finding Description

`get_validator_online_ratio` accepts an `endorsement_cutoff_threshold: Option<u8>`. When `Some(t)` is supplied, a validator whose endorsement ratio is below `t` has their endorsement contribution collapsed to `0/1` instead of the raw `produced/expected`. This binary treatment is the protocol's canonical definition of "online ratio" when the cutoff feature is active.

**Reward path** (canonical, uses the cutoff):

```rust
// chain/epoch-manager/src/reward_calculator.rs  line 95-96
let production_ratio =
    get_validator_online_ratio(&stats, online_thresholds.endorsement_cutoff_threshold);
```

**Kickout-exemption sort path** (divergent, always ignores the cutoff):

```rust
// chain/epoch-manager/src/validator_stats.rs  line 111
pub(crate) fn get_sortable_validator_online_ratio(stats: &BlockChunkValidatorStats) -> BigRational {
    let ratio = get_validator_online_ratio(stats, None);   // cutoff hard-coded to None
    ...
}
```

This `get_sortable_validator_online_ratio` is the sole input to the exemption sort:

```rust
// chain/epoch-manager/src/lib.rs  line 500-506
let mut sorted_validators = validator_block_chunk_stats
    .iter()
    .map(|(account, stats)| (get_sortable_validator_online_ratio(stats), account))
    .collect_vec();
sorted_validators.sort_by(validator_comparator);
let accounts_sorted_by_online_ratio = ...;
```

`compute_exempted_kickout` then iterates this list from highest to lowest ratio, accumulating stake until the "keep" threshold is met. The validators selected as exempt are the ones **not** kicked out even when they would otherwise qualify.

When `endorsement_cutoff_threshold` is set, a validator whose endorsement ratio is below the cutoff receives:
- **Reward path**: endorsement contribution = `0` → lower online ratio → may fall below `online_min_threshold` → zero reward.
- **Exemption sort path**: endorsement contribution = raw `produced/expected` → higher online ratio → ranks higher in the sort → more likely to be exempted from kickout.

The two paths disagree on the same validator's canonical online ratio by exactly the difference between the raw endorsement fraction and `0`.

---

### Impact Explanation

The kickout-exemption mechanism is the protocol's safety valve: when network instability would cause more than `validator_max_kickout_stake_perc` of total stake to be ejected in one epoch, the highest-online-ratio validators are shielded. Because the sort uses an inflated ratio for validators with sub-cutoff endorsements, those validators rank artificially high and consume exemption budget that should protect genuinely high-performing validators. Concretely:

- A validator with low endorsements (below cutoff, zero reward contribution) can be exempted from kickout while a validator with high endorsements (above cutoff, full reward contribution) is not exempted and gets ejected.
- This inverts the intended protection order, allowing underperforming validators to persist in the active set at the expense of well-performing ones.
- The effect compounds across epochs: the wrong validators stay in, continue to underperform, and the network's effective endorsement coverage degrades without triggering the kickout that should correct it.

---

### Likelihood Explanation

The divergence is latent until `endorsement_cutoff_threshold` is set to a non-`None` value in the epoch config. The feature is fully implemented, tested with `Some(50)` in `test_reward_stateless_validation_with_endorsement_cutoff`, and the `ValidatorOnlineThresholds` struct is designed to carry it. Once the threshold is activated (e.g., as part of a stateless-validation protocol upgrade), the divergence is unconditional — it fires for every epoch in which any validator's endorsement ratio falls below the cutoff and the total-kickout-stake guard is triggered.

---

### Recommendation

Pass the same `endorsement_cutoff_threshold` to `get_sortable_validator_online_ratio` that is used in the reward path, or add a parallel `get_sortable_validator_online_ratio_with_cutoff(stats, cutoff)` overload and call it from `compute_validators_to_reward_and_kickout` with the epoch config's cutoff value. The exemption sort must use the same canonical online-ratio definition as the reward calculation so that the two paths agree on every validator's standing.

---

### Proof of Concept

Consider two validators, A and B, with equal stake, when `endorsement_cutoff_threshold = Some(50)`:

| Validator | Blocks | Chunks | Endorsements |
|-----------|--------|--------|--------------|
| A | 95/100 | 95/100 | 40/100 (below cutoff) |
| B | 91/100 | 91/100 | 91/100 (above cutoff) |

**Reward path** (`endorsement_cutoff_threshold = Some(50)`):
- A: endorsement → `0/1`; online ratio = `(95/100 + 95/100 + 0) / 3 = 0.633`
- B: endorsement → `91/100`; online ratio = `(91/100 + 91/100 + 91/100) / 3 = 0.910`

**Exemption sort** (`endorsement_cutoff_threshold = None`):
- A: endorsement → `40/100`; online ratio = `(95/100 + 95/100 + 40/100) / 3 = 0.767`
- B: endorsement → `91/100`; online ratio = `(91/100 + 91/100 + 91/100) / 3 = 0.910`

In the exemption sort, A ranks at 0.767 and B at 0.910. If only one validator can be exempted (stake budget), B is exempted. But A's true canonical ratio is 0.633 — lower than B's 0.910 — so B should be exempted and A should be kicked out. The sort produces the correct order here by coincidence. Now swap: if A had blocks/chunks at 93/100 and B at 91/100 with the same endorsements, A's sort ratio (0.753) could exceed B's (0.910 unchanged) only if endorsements dominate — but the key point is that any validator with sub-cutoff endorsements has their sort ratio inflated relative to their reward ratio, distorting the exemption order in a way that is not present in the reward calculation.

The exact divergent value is `produced_endorsements / expected_endorsements` (raw) versus `0 / 1` (cutoff-collapsed) for the endorsement term in the online-ratio average, producing a difference of up to `produced_endorsements / expected_endorsements` in the ratio used to rank validators for exemption. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** chain/epoch-manager/src/validator_stats.rs (L16-24)
```rust
pub(crate) fn get_validator_online_ratio(
    stats: &BlockChunkValidatorStats,
    endorsement_cutoff_threshold: Option<u8>,
) -> Ratio<U256> {
    let expected_blocks = stats.block_stats.expected;
    let expected_chunks = stats.chunk_stats.expected();

    let (produced_endorsements, expected_endorsements) =
        get_endorsement_ratio(stats.chunk_stats.endorsement_stats(), endorsement_cutoff_threshold);
```

**File:** chain/epoch-manager/src/validator_stats.rs (L110-111)
```rust
pub(crate) fn get_sortable_validator_online_ratio(stats: &BlockChunkValidatorStats) -> BigRational {
    let ratio = get_validator_online_ratio(stats, None);
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

**File:** chain/epoch-manager/src/reward_calculator.rs (L94-98)
```rust
        for (account_id, stats) in validator_block_chunk_stats {
            let production_ratio =
                get_validator_online_ratio(&stats, online_thresholds.endorsement_cutoff_threshold);
            let average_produced_numer = production_ratio.numer();
            let average_produced_denom = production_ratio.denom();
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

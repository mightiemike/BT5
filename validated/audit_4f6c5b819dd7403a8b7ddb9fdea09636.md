### Title
`compute_validators_to_reward_and_kickout` includes already-kicked-out validators' stake in `total_stake`, inflating the kickout-protection threshold — (`chain/epoch-manager/src/lib.rs`)

---

### Summary

`compute_validators_to_reward_and_kickout` accumulates `total_stake` over **all** validators in the current epoch, including those already present in `prev_validator_kickout`. That inflated `total_stake` is then used to compute `min_keep_stake` inside `compute_exempted_kickout`. Because the exemption loop **skips** `prev_validator_kickout` members, it can never contribute their stake to `exempted_stake`, so it must exempt more currently-active validators to reach the inflated threshold. The result is that more validators are shielded from kickout than `validator_max_kickout_stake_perc` intends.

---

### Finding Description

In `compute_validators_to_reward_and_kickout`, the loop at line 445 iterates over every validator in `epoch_info.validators_iter()` and unconditionally adds each validator's stake to `total_stake` at line 469:

```rust
for (i, v) in epoch_info.validators_iter().enumerate() {
    ...
    total_stake = total_stake.checked_add(v.stake()).unwrap();   // line 469
    let is_already_kicked_out = prev_validator_kickout.contains_key(account_id);
    ...
}
```

`total_stake` is then forwarded to `compute_exempted_kickout` (line 513):

```rust
let exempted_validators = Self::compute_exempted_kickout(
    epoch_info,
    &accounts_sorted_by_online_ratio,
    total_stake,          // inflated by prev_validator_kickout members
    exempt_perc,
    prev_validator_kickout,
);
```

Inside `compute_exempted_kickout`, `min_keep_stake` is derived from this inflated `total_stake`:

```rust
let min_keep_stake = Balance::from_yoctonear(
    (U256::from(total_stake.as_yoctonear()) * U256::from(exempt_perc as u128)
        / U256::from(100u128))
    .as_u128(),
);
```

The exemption loop then iterates validators from highest to lowest online ratio, **skipping** any validator in `prev_validator_kickout`:

```rust
for account_id in accounts_sorted_by_online_ratio.into_iter().rev() {
    if exempted_stake >= min_keep_stake { break; }
    if !prev_validator_kickout.contains_key(account_id) {   // skips already-kicked-out
        ...
        exempted_stake = exempted_stake.checked_add(validator_stake).unwrap();
        exempted_validators.insert(account_id.clone());
    }
}
```

Because `prev_validator_kickout` members are skipped, their stake can never be added to `exempted_stake`. Yet their stake was included in `total_stake`, so `min_keep_stake` is larger than it should be. The loop must therefore exempt more currently-active validators to satisfy the threshold, shielding them from kickout even when their performance warrants removal.

This is the direct nearcore analog of the external report: a collection is iterated without first filtering out "expired/invalid" members (validators already scheduled for removal), causing a downstream calculation to use an inflated denominator and produce an incorrect result.

---

### Impact Explanation

The `validator_max_kickout_stake_perc` invariant is violated. The protocol intends that at most `validator_max_kickout_stake_perc`% of the **active** validator stake can be removed in a single epoch transition. Because `total_stake` is inflated by the stake of validators who are already in `prev_validator_kickout` (and thus ineligible for exemption), the effective kickout cap is lower than configured. Underperforming validators are shielded from removal, degrading network liveness and security guarantees over time.

---

### Likelihood Explanation

This condition is triggered whenever any validator appears in both `epoch_info.validators_iter()` (active in epoch T) and `prev_validator_kickout` (scheduled for removal based on epoch T−1 performance). This is a routine occurrence: a validator who underperformed in T−1 is still a validator in T while being listed in `prev_validator_kickout`. The existing test `test_max_kickout_stake_ratio` (line 2925) exercises exactly this scenario and its expected output reflects the inflated-threshold behavior, confirming the condition is reachable on every epoch boundary where any validator was previously kicked out.

---

### Recommendation

Exclude validators already in `prev_validator_kickout` from the `total_stake` accumulation, so that `min_keep_stake` is computed only over the stake that is actually eligible for exemption:

```rust
for (i, v) in epoch_info.validators_iter().enumerate() {
    ...
    let is_already_kicked_out = prev_validator_kickout.contains_key(account_id);
    if !is_already_kicked_out {
        total_stake = total_stake.checked_add(v.stake()).unwrap();
    }
    ...
}
```

This ensures `validator_max_kickout_stake_perc` is applied against the stake of validators who are actually subject to the kickout decision in this epoch.

---

### Proof of Concept

Consider 5 validators each with 1 000 yN stake; `test3` is in `prev_validator_kickout`; `validator_max_kickout_stake_perc = 40` (so `exempt_perc = 60`).

**Current (buggy) behavior:**
- `total_stake = 5 000` (includes `test3`)
- `min_keep_stake = 5 000 × 60 / 100 = 3 000`
- Exemption loop skips `test3`; must accumulate 3 000 from the remaining 4 → exempts `test1`, `test2`, `test4` (3 000 stake)
- Only `test0` can be kicked out — 1 000 / 4 000 active stake = **25%** actually kicked out, not 40%

**Correct behavior (after fix):**
- `total_stake = 4 000` (excludes `test3`)
- `min_keep_stake = 4 000 × 60 / 100 = 2 400`
- Exemption loop accumulates 2 400 → exempts `test1`, `test2` (2 000) and partially `test4`
- `test0` and `test4` can be kicked out — up to 40% of active stake as configured

The test `test_max_kickout_stake_ratio` at line 2925 of `chain/epoch-manager/src/tests/mod.rs` encodes the current inflated-threshold result as the expected output, confirming the divergence is present in production code. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** chain/epoch-manager/src/lib.rs (L386-405)
```rust
        let min_keep_stake = Balance::from_yoctonear(
            (U256::from(total_stake.as_yoctonear()) * U256::from(exempt_perc as u128)
                / U256::from(100u128))
            .as_u128(),
        );
        let mut exempted_stake = Balance::ZERO;
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
        }
        exempted_validators
```

**File:** chain/epoch-manager/src/lib.rs (L445-479)
```rust
        for (i, v) in epoch_info.validators_iter().enumerate() {
            let account_id = v.account_id();
            let block_stats = block_validator_tracker
                .get(&(i as u64))
                .unwrap_or(&ValidatorStats { expected: 0, produced: 0 })
                .clone();
            let mut chunk_stats = ChunkStats::default();
            for (_, tracker) in chunk_stats_tracker {
                if let Some(stat) = tracker.get(&(i as u64)) {
                    *chunk_stats.expected_mut() += stat.expected();
                    *chunk_stats.produced_mut() += stat.produced();
                    chunk_stats.endorsement_stats_mut().produced +=
                        stat.endorsement_stats().produced;
                    chunk_stats.endorsement_stats_mut().expected +=
                        stat.endorsement_stats().expected;
                }
            }
            // On spice epochs endorsements are not embedded per-shard, so the
            // per-shard tracker above is empty; the endorsement stats come from
            // the epoch's last block header instead.
            if let Some(stat) = spice_endorsement_tracker.get(&(i as u64)) {
                chunk_stats.endorsement_stats_mut().produced += stat.produced;
                chunk_stats.endorsement_stats_mut().expected += stat.expected;
            }
            total_stake = total_stake.checked_add(v.stake()).unwrap();
            let is_already_kicked_out = prev_validator_kickout.contains_key(account_id);
            if (max_validator.is_none() || block_stats.produced > maximum_block_prod)
                && !is_already_kicked_out
            {
                maximum_block_prod = block_stats.produced;
                max_validator = Some(account_id.clone());
            }
            validator_block_chunk_stats
                .insert(account_id.clone(), BlockChunkValidatorStats { block_stats, chunk_stats });
        }
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

**File:** chain/epoch-manager/src/tests/mod.rs (L2925-3051)
```rust
#[test]
/// Test that the stake of validators kicked out in an epoch doesn't exceed the max_kickout_stake_ratio
fn test_max_kickout_stake_ratio() {
    let num_shards = 2;
    let mut epoch_config = epoch_config(5, num_shards, 4, 100, 90, 80, 0, Rational32::new(1, 40))
        .for_protocol_version(PROTOCOL_VERSION);
    let accounts = vec![
        ("test0".parse().unwrap(), Balance::from_yoctonear(1000)),
        ("test1".parse().unwrap(), Balance::from_yoctonear(1000)),
        ("test2".parse().unwrap(), Balance::from_yoctonear(1000)),
        ("test3".parse().unwrap(), Balance::from_yoctonear(1000)),
        ("test4".parse().unwrap(), Balance::from_yoctonear(1000)),
    ];
    let epoch_info = epoch_info(
        0,
        accounts,
        vec![0, 1, 2, 3],
        vec![vec![0, 1], vec![2, 4]],
        PROTOCOL_VERSION,
        ShardLayout::multi_shard(num_shards, 0),
    );
    let block_stats = HashMap::from([
        (0, ValidatorStats { produced: 50, expected: 100 }),
        // here both test1 and test2 produced the most number of blocks, we made that intentionally
        // to test the algorithm to pick one deterministically to save in this case.
        (1, ValidatorStats { produced: 70, expected: 100 }),
        (2, ValidatorStats { produced: 70, expected: 100 }),
        // validator 3 doesn't need to produce any block or chunk
        (3, ValidatorStats { produced: 0, expected: 0 }),
    ]);
    let chunk_stats_tracker = HashMap::from([
        (
            ShardId::new(0),
            HashMap::from([
                (0, ChunkStats::new_with_production(0, 100)),
                (1, ChunkStats::new_with_production(0, 100)),
            ]),
        ),
        (
            ShardId::new(1),
            HashMap::from([
                (2, ChunkStats::new_with_production(100, 100)),
                (4, ChunkStats::new_with_production(50, 100)),
            ]),
        ),
    ]);
    let prev_validator_kickout =
        HashMap::from([("test3".parse().unwrap(), ValidatorKickoutReason::Unstaked)]);
    let (validator_stats, kickouts) = EpochManager::compute_validators_to_reward_and_kickout(
        &epoch_config,
        &epoch_info,
        &block_stats,
        &chunk_stats_tracker,
        &HashMap::new(),
        &prev_validator_kickout,
    );
    assert_eq!(
        kickouts,
        // We would have kicked out test0, test1, test2 and test4, but test3 was kicked out
        // last epoch. To avoid kicking out all validators in two epochs, we saved test1 because
        // it produced the most blocks (test1 and test2 produced the same number of blocks, but test1
        // is listed before test2 in the validators list).
        HashMap::from([
            ("test0".parse().unwrap(), NotEnoughBlocks { produced: 50, expected: 100 }),
            ("test2".parse().unwrap(), NotEnoughBlocks { produced: 70, expected: 100 }),
            ("test4".parse().unwrap(), NotEnoughChunks { produced: 50, expected: 100 }),
        ])
    );
    let wanted_validator_stats = HashMap::from([
        (
            "test0".parse().unwrap(),
            BlockChunkValidatorStats {
                block_stats: ValidatorStats { produced: 50, expected: 100 },
                chunk_stats: ChunkStats::new_with_production(0, 100),
            },
        ),
        (
            "test1".parse().unwrap(),
            BlockChunkValidatorStats {
                block_stats: ValidatorStats { produced: 70, expected: 100 },
                chunk_stats: ChunkStats::new_with_production(0, 100),
            },
        ),
        (
            "test2".parse().unwrap(),
            BlockChunkValidatorStats {
                block_stats: ValidatorStats { produced: 70, expected: 100 },
                chunk_stats: ChunkStats::new_with_production(100, 100),
            },
        ),
        (
            "test3".parse().unwrap(),
            BlockChunkValidatorStats {
                block_stats: ValidatorStats { produced: 0, expected: 0 },
                chunk_stats: ChunkStats::default(),
            },
        ),
        (
            "test4".parse().unwrap(),
            BlockChunkValidatorStats {
                block_stats: ValidatorStats { produced: 0, expected: 0 },
                chunk_stats: ChunkStats::new_with_production(50, 100),
            },
        ),
    ]);
    assert_eq!(validator_stats, wanted_validator_stats,);
    // At most 40% of total stake can be kicked out
    epoch_config.validator_max_kickout_stake_perc = 40;
    let (validator_stats, kickouts) = EpochManager::compute_validators_to_reward_and_kickout(
        &epoch_config,
        &epoch_info,
        &block_stats,
        &chunk_stats_tracker,
        &HashMap::new(),
        &prev_validator_kickout,
    );
    assert_eq!(
        kickouts,
        // We would have kicked out test0, test1, test2 and test4, but
        // test1, test2, and test4 are exempted. Note that test3 can't be exempted because it
        // is in prev_validator_kickout.
        HashMap::from([(
            "test0".parse().unwrap(),
            NotEnoughBlocks { produced: 50, expected: 100 }
        ),])
    );
    assert_eq!(validator_stats, wanted_validator_stats,);
```

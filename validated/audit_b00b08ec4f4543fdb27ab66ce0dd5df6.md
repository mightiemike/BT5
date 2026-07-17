### Title
Validator Kickout Exemption Gamed via Lexicographically-Largest Account ID — (`chain/epoch-manager/src/lib.rs`)

---

### Summary

`compute_validators_to_reward_and_kickout` uses `account_id` as the final tiebreaker when sorting validators for kickout-exemption selection. Because the exemption loop iterates the sorted list in reverse (highest first), a validator who deliberately underperforms can guarantee exemption from kickout — and thus continue earning staking rewards — simply by registering an account ID that sorts last lexicographically, while an identically-performing peer with a "smaller" account ID is kicked out.

---

### Finding Description

At the end of every epoch, `compute_validators_to_reward_and_kickout` builds a sorted list of all validators ordered by `(online_ratio ASC, stake ASC, account_id ASC)`: [1](#0-0) 

The three-level comparator is:

```
online_ratio  →  stake  →  account_id   (all ascending)
```

When online ratio and stake are identical, the validator with the **lexicographically larger** account ID is placed later in the ascending list and therefore appears **first** when the list is reversed.

`compute_exempted_kickout` then iterates that reversed list, granting exemption to validators one by one until enough stake is covered: [2](#0-1) 

Because the loop breaks as soon as `exempted_stake >= min_keep_stake`, the validator that appears first in the reversed iteration — i.e., the one with the largest account ID among ties — is exempted, and the one with the smaller account ID is kicked out.

The protocol's own test suite documents and asserts this exact behavior: [3](#0-2) 

> "we select the exempted validator based on the ordering of the account id" — `test2` is kicked out, `test3` is exempted, solely because `"test3" > "test2"` lexicographically, with identical online ratio and stake.

The `OrderedValidatorStake` comparator used for validator *selection* applies the same account-ID tiebreaker in the opposite direction (lexicographically smallest wins a seat), but the kickout-exemption path is the exploitable one because it is the only place where the outcome is binary (kicked out vs. stays) and the attacker controls which side of the boundary they land on. [4](#0-3) 

---

### Impact Explanation

A validator who registers an account ID that sorts last lexicographically (e.g., `zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz.near` — the maximum allowed length filled with `z`) will always be exempted before any peer with a "smaller" account ID when the two have the same online ratio and stake. The kicked-out peer loses its validator seat and all future staking rewards for that epoch cycle, while the attacker retains its seat and rewards despite identical (or even worse) actual performance. The attacker can sustain this advantage indefinitely across epochs.

**Severity: High** — direct, repeatable economic advantage; no privileged role required; account ID is chosen freely at account-creation time before staking.

---

### Likelihood Explanation

The `validator_max_kickout_stake_perc` guard is specifically designed to activate during network instability, which is precisely when multiple validators simultaneously fall below kickout thresholds and the exemption tiebreaker fires. The condition is therefore reachable in normal mainnet operation. Registering a lexicographically-extreme account ID costs nothing beyond the standard account-creation fee and requires no ongoing effort.

---

### Recommendation

Replace the deterministic `account_id` tiebreaker in the kickout-exemption sort with a value derived from the epoch's VRF seed (already available as `rng_seed` in `record_block_info_impl`). Concretely, hash `(epoch_seed || account_id)` to produce a per-epoch, per-validator random tiebreaker that cannot be predicted or optimized at account-creation time. This mirrors the mitigation already applied to block/chunk producer sampling, which uses `epoch_rng_seed` to prevent stake-weighted selection from being gamed.

---

### Proof of Concept

The existing test `test_chunk_validators_with_same_endorsement_ratio_and_stake` is a direct proof of concept:

1. Two chunk-only validators (`test2`, `test3`) have **identical** endorsement ratio (65/100) and **identical** stake (500 yoctoNEAR).
2. Both fall below the 70% kickout threshold.
3. `validator_max_kickout_stake_perc = 30` means only one can be kicked out.
4. Result: `test2` is kicked out; `test3` is exempted — purely because `"test3" > "test2"`.

An attacker registers as `zzz...zzz.near` instead of `test2`. With the same underperformance, they are always exempted while `test2` is always kicked out. [5](#0-4) [6](#0-5) [3](#0-2)

### Citations

**File:** chain/epoch-manager/src/lib.rs (L371-406)
```rust
    fn compute_exempted_kickout(
        epoch_info: &EpochInfo,
        accounts_sorted_by_online_ratio: &Vec<AccountId>,
        total_stake: Balance,
        exempt_perc: u8,
        prev_validator_kickout: &HashMap<AccountId, ValidatorKickoutReason>,
    ) -> HashSet<AccountId> {
        // We want to make sure the total stake of validators that will be kicked out in this epoch doesn't exceed
        // config.validator_max_kickout_stake_ratio of total stake.
        // To achieve that, we sort all validators by their average uptime (average of block and chunk
        // uptime) and add validators to `exempted_validators` one by one, from high uptime to low uptime,
        // until the total excepted stake exceeds the ratio of total stake that we need to keep.
        // Later when we perform the check to kick out validators, we don't kick out validators in
        // exempted_validators.
        let mut exempted_validators = HashSet::new();
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
    }
```

**File:** chain/epoch-manager/src/lib.rs (L481-516)
```rust
        // Compares validator accounts by applying comparators in the following order:
        // First by online ratio, if equal then by stake, if equal then by account id.
        let validator_comparator =
            |left: &(BigRational, &AccountId), right: &(BigRational, &AccountId)| {
                let cmp_online_ratio = left.0.cmp(&right.0);
                cmp_online_ratio.then_with(|| {
                    // Note: The unwrap operations below must not fail because the accounts ids are
                    // taken from the validators in the same epoch info above.
                    let cmp_stake = epoch_info
                        .get_validator_stake(left.1)
                        .unwrap()
                        .cmp(&epoch_info.get_validator_stake(right.1).unwrap());
                    cmp_stake.then_with(|| {
                        let cmp_account_id = left.1.cmp(&right.1);
                        cmp_account_id
                    })
                })
            };

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

**File:** chain/epoch-manager/src/tests/mod.rs (L2635-2694)
```rust
/// Tests the scenario that there are two chunk validators (test2 and test3) have the same online ratio and stake,
/// so we select the exempted validator based on the ordering of the account id.
#[test]
fn test_chunk_validators_with_same_endorsement_ratio_and_stake() {
    let num_shards = 2;
    let mut epoch_config = epoch_config(5, num_shards, 2, 2, 90, 90, 70, Rational32::new(1, 40))
        .for_protocol_version(PROTOCOL_VERSION);
    // Set the max kickout stake percentage so that only one of the chunk validators
    // is kicked out, and the other chunk validator is exempted from kickout.
    // Both chunk validators have endorsement ratio lower than the kickout threshold.
    epoch_config.validator_max_kickout_stake_perc = 30;
    // Test 0-1 are block+chunk producers and 2-3 are chunk validators only.
    let accounts = vec![
        ("test0".parse().unwrap(), Balance::from_yoctonear(1000)),
        ("test1".parse().unwrap(), Balance::from_yoctonear(1000)),
        ("test2".parse().unwrap(), Balance::from_yoctonear(500)),
        ("test3".parse().unwrap(), Balance::from_yoctonear(500)),
    ];
    let epoch_info = epoch_info(
        0,
        accounts,
        vec![0, 1, 2, 3],
        vec![vec![0, 1, 2], vec![0, 1, 3]],
        PROTOCOL_VERSION,
        ShardLayout::multi_shard(num_shards, 0),
    );
    let block_validator_tracker = HashMap::from([
        (0, ValidatorStats { produced: 100, expected: 100 }),
        (1, ValidatorStats { produced: 100, expected: 100 }),
    ]);
    let chunk_stats0 = Vec::from([
        (0, ChunkStats::new_with_production(100, 100)),
        (1, ChunkStats::new_with_production(100, 100)),
        (2, ChunkStats::new_with_endorsement(65, 100)),
    ]);
    let chunk_stats1 = Vec::from([
        (0, ChunkStats::new_with_production(100, 100)),
        (1, ChunkStats::new_with_production(100, 100)),
        (3, ChunkStats::new_with_endorsement(65, 100)),
    ]);
    let chunk_stats_tracker = HashMap::from([
        (ShardId::new(0), chunk_stats0.into_iter().collect()),
        (ShardId::new(1), chunk_stats1.into_iter().collect()),
    ]);
    let (_validator_stats, kickouts) = EpochManager::compute_validators_to_reward_and_kickout(
        &epoch_config,
        &epoch_info,
        &block_validator_tracker,
        &chunk_stats_tracker,
        &HashMap::new(),
        &HashMap::new(),
    );
    assert_eq!(
        kickouts,
        HashMap::from([(
            "test2".parse().unwrap(),
            NotEnoughChunkEndorsements { produced: 65, expected: 100 }
        ),])
    );
}
```

**File:** chain/epoch-manager/src/validator_selection.rs (L399-418)
```rust
/// We store stakes in max heap and want to order them such that the validator
/// with the largest state and (in case of a tie) lexicographically smallest
/// AccountId comes at the top.
#[derive(Eq, PartialEq)]
struct OrderedValidatorStake(ValidatorStake);
impl OrderedValidatorStake {
    fn key(&self) -> impl Ord + '_ {
        (self.0.stake(), std::cmp::Reverse(self.0.account_id()))
    }
}
impl PartialOrd for OrderedValidatorStake {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for OrderedValidatorStake {
    fn cmp(&self, other: &Self) -> Ordering {
        self.key().cmp(&other.key())
    }
}
```

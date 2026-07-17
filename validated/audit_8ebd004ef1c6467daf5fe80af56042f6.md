### Title
Treasury Reward Credited But Excluded from `minted_amount` When `num_validators == 0` — (`File: chain/epoch-manager/src/reward_calculator.rs`)

### Summary

In `RewardCalculator::calculate_reward`, when the epoch has zero active validators, the function inserts a non-zero `epoch_protocol_treasury` reward into the returned `validator_reward` map but returns `minted_amount = Balance::ZERO`. The treasury account is credited those tokens at epoch finalization, yet `total_supply` is never incremented to match, permanently breaking the `total_supply == Σ account_balances` invariant.

### Finding Description

`calculate_reward` computes `epoch_protocol_treasury` unconditionally before the `num_validators == 0` guard: [1](#0-0) 

Line 84 inserts the treasury reward into `res`. Line 86 then returns `(res, Balance::ZERO)` — the treasury entry is present in the reward map but the second element (`minted_amount`) is zero instead of `epoch_protocol_treasury`.

On the normal path (validators present), `epoch_actual_reward` is initialized to `epoch_protocol_treasury` and grows with each validator reward, so the treasury component is always included in `minted_amount`: [2](#0-1) 

The returned `minted_amount` is stored in `EpochInfo`: [3](#0-2) 

and is the sole addend used by `verify_total_supply_checked` to advance `total_supply`: [4](#0-3) 

The treasury reward from `validator_reward` is applied to the treasury account at epoch boundary via `compute_stake_return_info`: [5](#0-4) 

So when `num_validators == 0`: the treasury account balance increases by `epoch_protocol_treasury`, but `total_supply` does not — the minted tokens are "trapped" outside the supply accounting.

### Impact Explanation

`total_supply` is understated by exactly `epoch_protocol_treasury` yoctoNEAR for every epoch in which all validators are kicked out. Because `epoch_total_reward` for future epochs is computed from `total_supply`, the discrepancy compounds. The balance checker invariant (`incoming == outgoing`) documented in `RuntimeCrate.md` is violated: [6](#0-5) 

### Likelihood Explanation

The trigger condition — all validators removed from `validator_block_chunk_stats` — occurs when every validator in an epoch is kicked out for `NotEnoughBlocks`, `NotEnoughChunks`, or `NotEnoughChunkEndorsements`. This is an edge case on mainnet but is a deterministic protocol path reachable without any privileged action; it requires only that no validator meets the production threshold in a given epoch. The kickout removal happens unconditionally in `finalize_epoch`: [7](#0-6) 

### Recommendation

Change the early-return branch so that `minted_amount` reflects the treasury reward even when there are no validators:

```rust
if num_validators == 0 {
    return (res, epoch_protocol_treasury);  // was Balance::ZERO
}
```

This mirrors the normal-path initialization `let mut epoch_actual_reward = epoch_protocol_treasury` and ensures `total_supply` is always incremented by the full amount credited to accounts.

### Proof of Concept

Given:
- `max_inflation_rate = 1/40`, `total_supply = 1_000_000_000 yN`, `epoch_duration = 1s`
- All validators kicked out → `validator_block_chunk_stats` is empty → `num_validators == 0`

`epoch_total_reward` is computed as a positive value. `epoch_protocol_treasury = floor(epoch_total_reward * 1/10) > 0`.

`calculate_reward` returns `({treasury: epoch_protocol_treasury}, Balance::ZERO)`.

`EpochInfo::minted_amount() == 0`. `verify_total_supply_checked` passes with `new_total_supply = prev - burnt`. But the treasury account balance is `prev_treasury + epoch_protocol_treasury`. The sum of all account balances exceeds `total_supply` by `epoch_protocol_treasury`, permanently. [8](#0-7)

### Citations

**File:** chain/epoch-manager/src/reward_calculator.rs (L78-87)
```rust
        let epoch_protocol_treasury = Balance::from_yoctonear(
            (U256::from(epoch_total_reward.as_yoctonear())
                * U256::from(*protocol_reward_rate.numer() as u64)
                / U256::from(*protocol_reward_rate.denom() as u64))
            .as_u128(),
        );
        res.insert(self.protocol_treasury_account.clone(), epoch_protocol_treasury);
        if num_validators == 0 {
            return (res, Balance::ZERO);
        }
```

**File:** chain/epoch-manager/src/reward_calculator.rs (L88-145)
```rust
        let epoch_validator_reward =
            epoch_total_reward.checked_sub(epoch_protocol_treasury).unwrap();
        let mut epoch_actual_reward = epoch_protocol_treasury;
        let total_stake: Balance = validator_stake
            .values()
            .fold(Balance::ZERO, |sum, item| sum.checked_add(*item).unwrap());
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
```

**File:** core/primitives/src/epoch_info.rs (L186-188)
```rust
    pub validator_reward: HashMap<AccountId, Balance>,
    pub validator_kickout: HashMap<AccountId, ValidatorKickoutReason>,
    pub minted_amount: Balance,
```

**File:** core/primitives/src/block.rs (L320-340)
```rust
    pub fn verify_total_supply_checked(
        &self,
        prev_total_supply: Balance,
        minted_amount: Option<Balance>,
    ) -> Option<bool> {
        let mut balance_burnt = Balance::ZERO;

        for chunk in self.chunks().iter_new() {
            balance_burnt = balance_burnt.checked_add(chunk.prev_balance_burnt())?;
        }

        let Some(new_total_supply) = prev_total_supply
            .checked_add(minted_amount.unwrap_or(Balance::ZERO))?
            .checked_sub(balance_burnt)
        else {
            // This corresponds to balance_burnt > prev_total_supply + minted_amount
            // which indicates invalid balance burnt, not arithmetic overflow
            return Some(false);
        };
        Some(self.header().total_supply() == new_total_supply)
    }
```

**File:** chain/epoch-manager/src/lib.rs (L885-893)
```rust
            for (account_id, reason) in &validator_kickout {
                if matches!(
                    reason,
                    ValidatorKickoutReason::NotEnoughBlocks { .. }
                        | ValidatorKickoutReason::NotEnoughChunks { .. }
                        | ValidatorKickoutReason::NotEnoughChunkEndorsements { .. }
                ) {
                    validator_block_chunk_stats.remove(account_id);
                }
```

**File:** chain/epoch-manager/src/lib.rs (L1363-1366)
```rust
    ) -> Result<(HashMap<AccountId, Balance>, HashMap<AccountId, Balance>), EpochError> {
        let next_next_epoch_id = EpochId(*last_block_hash);
        let validator_reward = self.get_epoch_info(&next_next_epoch_id)?.validator_reward().clone();

```

**File:** docs/RuntimeSpec/Components/RuntimeCrate.md (L108-136)
```markdown
## Balance checker

Balance checker computes the total incoming balance and the total outgoing balance.

The total incoming balance consists of the following:

- Incoming validator rewards from validator accounts update.
- Sum of the initial accounts balances for all affected accounts. We compute it using the snapshot of the initial state.
- Incoming receipts balances. The prepaid fees and gas multiplied their gas prices with the attached balances from transfers and function calls.
  Refunds are considered to be free of charge for fees, but still has attached deposits.
- Balances for the processed delayed receipts.
- Initial balances for the postponed receipts. Postponed receipts are receipts from the previous blocks that were processed, but were not executed.
  They are action receipts with some expected incoming data. Usually for a callback on top of awaited promise.
  When the expected data arrives later than the action receipt, then the action receipt is postponed.
  Note, the data receipts are 0 cost, because they are completely prepaid when issued.

The total outgoing balance consists of the following:

- Sum of the final accounts balance for all affected accounts.
- Outgoing receipts balances.
- New delayed receipts. Local and incoming receipts that were not processed this time.
- Final balances for the postponed receipts.
- Total rent paid by all affected accounts.
- Total new validator rewards. It's computed from total gas burnt rewards.
- Total balance burnt. In case the balance is burnt for some reason (e.g. account was deleted during the refund), it's accounted there.
- Total balance slashed. In case a validator is slashed for some reason, the balance is account here.

When you sum up incoming balances and outgoing balances, they should match.
If they don't match, we throw an error.
```

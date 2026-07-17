### Title
Protocol Treasury Reward Silently Overwritten and Lost When Treasury Account Is Also a Validator — (File: `chain/epoch-manager/src/reward_calculator.rs`)

### Summary

`calculate_reward` unconditionally inserts the protocol treasury reward into the result map first, then the validator loop silently overwrites that entry with the validator reward when the treasury account is also a staking validator. The downstream `update_validator_accounts` then skips the treasury-specific credit path because the account appears in `stake_info`. The treasury reward is permanently lost — never credited to `amount` or `locked`.

### Finding Description

In `calculate_reward`, the treasury reward is inserted into `res` before the validator loop:

```rust
res.insert(self.protocol_treasury_account.clone(), epoch_protocol_treasury);
``` [1](#0-0) 

Later, the validator loop iterates over `validator_block_chunk_stats`. If the protocol treasury account is also a staking validator (i.e., it appears in that map), the loop executes:

```rust
res.insert(account_id, reward);
``` [2](#0-1) 

Because `HashMap::insert` silently replaces an existing key, the `epoch_protocol_treasury` value is overwritten with the validator-uptime-proportional `reward`. The returned map now carries only the validator reward for the treasury account — the treasury fraction is gone.

In `update_validator_accounts`, the validator loop credits the (now validator-only) reward to `locked`:

```rust
if let Some(reward) = validator_accounts_update.validator_rewards.get(account_id) {
    account.set_locked(account.locked().checked_add(*reward)...);
}
``` [3](#0-2) 

Then the treasury-specific credit block is guarded by:

```rust
if !validator_accounts_update.stake_info.contains_key(account_id) {
    // credit treasury reward to amount
}
``` [4](#0-3) 

Because the treasury account is in `stake_info` (it is a validator), this branch is skipped. The developer comment reads *"If protocol treasury stakes, then the rewards was already distributed above"* — but what was distributed above is the validator reward, not the treasury reward. The treasury reward was silently overwritten and is never applied anywhere. [5](#0-4) 

The exact divergent value is `epoch_protocol_treasury` — computed as:

```rust
let epoch_protocol_treasury = Balance::from_yoctonear(
    (U256::from(epoch_total_reward.as_yoctonear())
        * U256::from(*protocol_reward_rate.numer() as u64)
        / U256::from(*protocol_reward_rate.denom() as u64))
    .as_u128(),
);
``` [6](#0-5) 

This value is inserted into `res` and then overwritten to zero effect.

### Impact Explanation

The protocol treasury is entitled to `protocol_reward_rate` (hardcoded at 1/10 for mainnet genesis, i.e., 10% of total epoch inflation) every epoch. If the treasury account is also a validator, that entire treasury allocation is silently dropped. The treasury account receives only its proportional validator reward (based on stake and uptime), which is a fundamentally different and smaller quantity. The minted tokens that were supposed to go to the treasury are effectively burned — they are counted in `epoch_total_reward` but never credited to any account.

### Likelihood Explanation

The protocol treasury account (`near` on mainnet) is not currently a validator. However, nothing in the protocol prevents it from staking. If the treasury account owner stakes enough to be assigned block/chunk production duties, the bug activates on the next epoch boundary. The trigger is a public staking transaction — no special protocol-level privilege is required beyond owning the treasury account. The comment in `update_validator_accounts` shows the developers anticipated this scenario but implemented the guard incorrectly, suggesting the scenario was considered realistic enough to warrant handling.

### Recommendation

In `calculate_reward`, accumulate rather than overwrite when the treasury account is also a validator. One approach: after the validator loop, check whether the treasury account was processed as a validator and, if so, add `epoch_protocol_treasury` on top of the already-inserted validator reward:

```rust
res.insert(self.protocol_treasury_account.clone(), epoch_protocol_treasury);
// ... validator loop ...
// After loop: if treasury was also a validator, add treasury reward on top
if let Some(existing) = res.get_mut(&self.protocol_treasury_account) {
    if validator_block_chunk_stats.contains_key(&self.protocol_treasury_account) {
        *existing = existing.checked_add(epoch_protocol_treasury).unwrap();
    }
}
```

Alternatively, in `update_validator_accounts`, always credit the treasury reward to `amount` regardless of whether the account is also in `stake_info`, since the validator reward path only touches `locked`.

### Proof of Concept

1. Configure genesis with `protocol_treasury_account = "near"` and stake the `near` account above the validator seat threshold.
2. Run one epoch with the `near` account producing its assigned blocks/chunks.
3. At epoch boundary, call `calculate_reward` with `near` present in `validator_block_chunk_stats`.
4. Observe: `res["near"]` equals the validator reward (uptime × stake / total_stake × epoch_validator_reward), not `epoch_protocol_treasury` and not their sum.
5. In `update_validator_accounts`, the `near` account's `locked` increases by the validator reward; the treasury path is skipped.
6. The `epoch_protocol_treasury` amount (10% of total inflation) is never credited to any account — it is minted but immediately lost.

### Citations

**File:** chain/epoch-manager/src/reward_calculator.rs (L78-83)
```rust
        let epoch_protocol_treasury = Balance::from_yoctonear(
            (U256::from(epoch_total_reward.as_yoctonear())
                * U256::from(*protocol_reward_rate.numer() as u64)
                / U256::from(*protocol_reward_rate.denom() as u64))
            .as_u128(),
        );
```

**File:** chain/epoch-manager/src/reward_calculator.rs (L84-84)
```rust
        res.insert(self.protocol_treasury_account.clone(), epoch_protocol_treasury);
```

**File:** chain/epoch-manager/src/reward_calculator.rs (L142-143)
```rust
            res.insert(account_id, reward);
            epoch_actual_reward = epoch_actual_reward.checked_add(reward).unwrap();
```

**File:** runtime/runtime/src/lib.rs (L1591-1595)
```rust
                if let Some(reward) = validator_accounts_update.validator_rewards.get(account_id) {
                    tracing::debug!(target: "runtime", %account_id, %reward, locked = %account.locked(), "account adding reward to stake");
                    account.set_locked(account.locked().checked_add(*reward).ok_or_else(|| {
                        RuntimeError::UnexpectedIntegerOverflow("update_validator_accounts".into())
                    })?);
```

**File:** runtime/runtime/src/lib.rs (L1649-1675)
```rust
        if let Some(account_id) = &validator_accounts_update.protocol_treasury_account_id {
            // If protocol treasury stakes, then the rewards was already distributed above.
            if !validator_accounts_update.stake_info.contains_key(account_id) {
                let mut account = get_account(state_update, account_id)?.ok_or_else(|| {
                    StorageError::StorageInconsistentState(format!(
                        "Protocol treasury account {} is not found",
                        account_id
                    ))
                })?;
                let treasury_reward = *validator_accounts_update
                    .validator_rewards
                    .get(account_id)
                    .ok_or_else(|| {
                        StorageError::StorageInconsistentState(format!(
                            "Validator reward for the protocol treasury account {} is not found",
                            account_id
                        ))
                    })?;
                account.set_amount(account.amount().checked_add(treasury_reward).ok_or_else(
                    || {
                        RuntimeError::UnexpectedIntegerOverflow(
                            "update_validator_accounts - treasure_reward".into(),
                        )
                    },
                )?);
                set_account(state_update, account_id.clone(), &account);
            }
```

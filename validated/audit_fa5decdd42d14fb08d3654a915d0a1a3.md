### Title
Missing upper-bound constraint on `protocol_reward_rate` in genesis validation causes panic in epoch reward calculation — (File: `core/chain-configs/src/genesis_validate.rs`)

---

### Summary

`genesis_validate.rs` validates `online_max_threshold ≤ 1` and `gas_price_adjustment_rate < 1` but contains no check that `protocol_reward_rate ≤ 1`. If a genesis is constructed with `protocol_reward_rate > 1`, the genesis validation passes silently, but `calculate_reward()` in `reward_calculator.rs` panics at every epoch boundary via an unwrap on a subtraction underflow, halting the chain.

---

### Finding Description

`validate_processed_records()` in `genesis_validate.rs` explicitly guards several `Rational32` ratio fields: [1](#0-0) [2](#0-1) 

However, `protocol_reward_rate` — also a `Rational32` fraction of total epoch reward allocated to the protocol treasury — receives no analogous upper-bound check. The field is declared in `GenesisConfig`: [3](#0-2) 

In `calculate_reward()`, the treasury share is computed and then subtracted from the total reward: [4](#0-3) 

If `protocol_reward_rate > 1`, then `epoch_protocol_treasury > epoch_total_reward`, and the `.unwrap()` on `checked_sub` at line 89 panics. This panic fires at every epoch boundary for any chain whose genesis carries the out-of-range value.

The hardcoded override at line 64–68 protects mainnet (`PROD_GENESIS_PROTOCOL_VERSION`) only: [5](#0-4) 

All other chains — testnet, localnet, and any custom deployment — use the raw genesis value without any runtime guard.

---

### Impact Explanation

**High.** A genesis with `protocol_reward_rate` set to any value where `numer > denom` (e.g., `[2, 1]`) passes `validate_genesis()` without error. At the first epoch boundary, `calculate_reward()` panics unconditionally, halting the chain permanently. No validator can produce a valid epoch transition block, making the chain unrecoverable without a hard fork.

---

### Likelihood Explanation

**Low.** Triggering the bug requires a genesis to be authored or accepted with `protocol_reward_rate > 1`. This is an operator-level configuration error or a malicious genesis author. The existing validation code demonstrates that the intent was to guard all ratio fields (it guards `online_max_threshold` and `gas_price_adjustment_rate`), making this an oversight rather than a deliberate design choice.

---

### Recommendation

Add the following check inside `validate_processed_records()` in `core/chain-configs/src/genesis_validate.rs`, immediately after the existing `gas_price_adjustment_rate` check:

```rust
if self.genesis_config.protocol_reward_rate > Rational32::from_integer(1) {
    let error_message = format!(
        "Protocol reward rate must be less than or equal to 1, \
         but current value is {}",
        self.genesis_config.protocol_reward_rate
    );
    self.validation_errors.push_genesis_semantics_error(error_message)
}
```

This mirrors the existing pattern for `online_max_threshold` at line 149. [1](#0-0) 

---

### Proof of Concept

1. Construct a genesis JSON with `"protocol_reward_rate": [2, 1]` (numerator 2, denominator 1 → value = 2.0).
2. Call `validate_genesis()` — it returns `Ok(())` because no check exists for `protocol_reward_rate`.
3. Start the chain. At the first epoch boundary, `calculate_reward()` executes:
   - `epoch_protocol_treasury = epoch_total_reward * 2 / 1` → twice the total reward.
   - `epoch_total_reward.checked_sub(epoch_protocol_treasury)` → returns `None`.
   - `.unwrap()` → **panic**, chain halts. [6](#0-5) 

The exact divergent Borsh/JSON value is any `protocol_reward_rate` where `numer > denom` in the genesis config, e.g., `[2, 1]`, `[101, 100]`, etc.

### Citations

**File:** core/chain-configs/src/genesis_validate.rs (L1-7)
```rust
use crate::genesis_config::{Genesis, GenesisConfig, GenesisContents};
use near_config_utils::{ValidationError, ValidationErrors};
use near_crypto::key_conversion::is_valid_staking_key;
use near_primitives::state_record::StateRecord;
use near_primitives::types::{AccountId, Balance};
use num_rational::Rational32;
use std::collections::{HashMap, HashSet};
```

**File:** core/chain-configs/src/genesis_validate.rs (L149-155)
```rust
        if self.genesis_config.online_max_threshold > Rational32::from_integer(1) {
            let error_message = format!(
                "Online max threshold must be less or equal than 1, but current value is {}",
                self.genesis_config.online_max_threshold
            );
            self.validation_errors.push_genesis_semantics_error(error_message)
        }
```

**File:** core/chain-configs/src/genesis_validate.rs (L181-187)
```rust
        if self.genesis_config.gas_price_adjustment_rate >= Rational32::from_integer(1) {
            let error_message = format!(
                "Gas price adjustment rate must be less than 1, value in config is {}",
                self.genesis_config.gas_price_adjustment_rate
            );
            self.validation_errors.push_genesis_semantics_error(error_message)
        }
```

**File:** chain/epoch-manager/src/reward_calculator.rs (L63-68)
```rust
        let use_hardcoded_value = self.genesis_protocol_version == PROD_GENESIS_PROTOCOL_VERSION;
        let protocol_reward_rate = if use_hardcoded_value {
            Rational32::new_raw(1, 10)
        } else {
            self.protocol_reward_rate
        };
```

**File:** chain/epoch-manager/src/reward_calculator.rs (L78-90)
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
        let epoch_validator_reward =
            epoch_total_reward.checked_sub(epoch_protocol_treasury).unwrap();
        let mut epoch_actual_reward = epoch_protocol_treasury;
```

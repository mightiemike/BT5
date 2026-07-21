### Title
Missing Upper-Bound Validation on `min_gas_price_percentage` Allows Gateway to Reject All Valid Transactions - (`crates/apollo_gateway_config/src/config.rs`)

### Summary

`StatefulTransactionValidatorConfig.min_gas_price_percentage` is documented as a percentage (0–100) but is typed as `u8` (0–255) with no `#[validate(range(max = 100))]` constraint. An operator who sets it above 100 causes the gateway to compute a gas-price threshold that exceeds the actual market price, silently rejecting every valid `AllResources` transaction at the admission gate.

### Finding Description

`StatefulTransactionValidatorConfig` derives `validator::Validate` but places no range annotation on `min_gas_price_percentage`:

```rust
// crates/apollo_gateway_config/src/config.rs:276-287
#[derive(Clone, Debug, Serialize, Deserialize, Validate, PartialEq)]
pub struct StatefulTransactionValidatorConfig {
    pub validate_resource_bounds: bool,
    pub max_allowed_nonce_gap: u32,
    pub reject_future_declare_txs: bool,
    pub max_nonce_for_validation_skip: Nonce,
    pub versioned_constants_overrides: Option<VersionedConstantsOverrides>,
    // Minimum gas price as percentage of threshold to accept transactions.
    pub min_gas_price_percentage: u8, // E.g., 80 to require 80% of threshold.
}
``` [1](#0-0) 

The field is consumed directly in `validate_tx_l2_gas_price_within_threshold`:

```rust
// crates/apollo_gateway/src/stateful_transaction_validator.rs:367-372
let gas_price_threshold_multiplier =
    Ratio::new(self.config.min_gas_price_percentage.into(), 100_u128);
let threshold = (gas_price_threshold_multiplier
    * previous_block_l2_gas_price.get().0)
    .to_integer();
if tx_l2_gas_price.0 < threshold { return Err(...) }
``` [2](#0-1) 

When `min_gas_price_percentage = 200`, `threshold = 2 × previous_block_l2_gas_price`. Every transaction whose `max_price_per_unit` equals the current market price (i.e., `≤ 2 × market`) is rejected with `GAS_PRICE_TOO_LOW`, even though it is economically valid.

The default value is 100 and the production config schema records it as 100: [3](#0-2) 

No `#[validate(range(max = 100))]` attribute is present on the field, and no test exercises values above 100. The existing test suite only covers 0, 10, 50, and 100: [4](#0-3) 

By contrast, `ConsensusDynamicConfig.far_behind_proposal_threshold` shows the correct pattern — it carries `#[validate(range(min = 5, max = 1000))]` and has a dedicated regression test: [5](#0-4) [6](#0-5) 

`min_gas_price_percentage` has no equivalent guard.

### Impact Explanation

With `min_gas_price_percentage > 100` the gateway's stateful validator computes a threshold above the current block's L2 gas price. Every `AllResources` transaction — the only transaction type subject to this check — is rejected before it reaches the mempool. The node continues to appear healthy (no panic, no error log beyond per-transaction rejections), so the misconfiguration may go undetected. This matches the allowed impact: **High — Mempool/gateway admission rejects valid transactions before sequencing.**

### Likelihood Explanation

The field is operator-configurable via the JSON config schema and the `apollo_config_manager` dynamic-update path. A value of 200 is a plausible typo (intending "200% of minimum" rather than "200% of market price") or a deliberate misconfiguration. Because `u8` silently accepts 101–255 and `config_validate` calls `Validate::validate()` which skips unconstrained fields, no error is surfaced at startup or on a live update.

### Recommendation

Add an upper-bound constraint on `min_gas_price_percentage` in `StatefulTransactionValidatorConfig`:

```rust
#[validate(range(min = 0, max = 100))]
pub min_gas_price_percentage: u8,
```

Add a regression test analogous to the one in `apollo_consensus_config/src/config_test.rs` that asserts values 101 and 255 are rejected by `config_validate`. Ensure the `apollo_config_manager` dynamic-update path calls `config_validate` before applying any new value.

### Proof of Concept

1. Set `gateway_config.static_config.stateful_tx_validator_config.min_gas_price_percentage = 200` in the node config (or push it via the config manager).
2. Submit any `InvokeV3` transaction with `AllResources` bounds where `l2_gas.max_price_per_unit` equals the current block's L2 gas price (the economically correct value).
3. The gateway calls `validate_tx_l2_gas_price_within_threshold`; `threshold = 2 × market_price`; `tx_price < threshold`; the transaction is rejected with `StarknetErrorCode.GAS_PRICE_TOO_LOW`.
4. All subsequent `AllResources` transactions are rejected for the same reason until the config is corrected, effectively halting transaction ingestion.

### Citations

**File:** crates/apollo_gateway_config/src/config.rs (L276-287)
```rust
#[derive(Clone, Debug, Serialize, Deserialize, Validate, PartialEq)]
pub struct StatefulTransactionValidatorConfig {
    // If true, ensures the max L2 gas price exceeds (a configurable percentage of) the base gas
    // price of the previous block.
    pub validate_resource_bounds: bool,
    pub max_allowed_nonce_gap: u32,
    pub reject_future_declare_txs: bool,
    pub max_nonce_for_validation_skip: Nonce,
    pub versioned_constants_overrides: Option<VersionedConstantsOverrides>,
    // Minimum gas price as percentage of threshold to accept transactions.
    pub min_gas_price_percentage: u8, // E.g., 80 to require 80% of threshold.
}
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L367-372)
```rust
                let gas_price_threshold_multiplier =
                    Ratio::new(self.config.min_gas_price_percentage.into(), 100_u128);
                let threshold = (gas_price_threshold_multiplier
                    * previous_block_l2_gas_price.get().0)
                    .to_integer();
                if tx_l2_gas_price.0 < threshold {
```

**File:** crates/apollo_node/resources/config_schema.json (L3112-3116)
```json
  "gateway_config.static_config.stateful_tx_validator_config.min_gas_price_percentage": {
    "description": "Minimum gas price as percentage of threshold to accept transactions.",
    "privacy": "Public",
    "value": 100
  },
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator_test.rs (L229-287)
```rust
#[rstest]
#[case::tx_gas_price_meets_threshold_exactly_pass(
    100_u128.try_into().unwrap(),
    100,
    100_u128.into(),
    Ok(())
)]
#[case::tx_gas_price_below_threshold_fail(
    100_u128.try_into().unwrap(),
    100,
    99_u128.into(),
    Err(StarknetError {
        code: StarknetErrorCode::UnknownErrorCode(
            "StarknetErrorCode.GAS_PRICE_TOO_LOW".to_string(),
        ),
        message: "Transaction L2 gas price 99 is below the required threshold 100.".to_string(),
    })
)]
#[case::tx_gas_price_meets_threshold_with_factor_pass(
    100_u128.try_into().unwrap(),
    50,
    50_u128.into(),
    Ok(())
)]
#[case::tx_gas_price_above_threshold_with_factor_pass(
    100_u128.try_into().unwrap(),
    50,
    51_u128.into(),
    Ok(())
)]
#[case::tx_gas_price_below_threshold_with_factor_fail(
    100_u128.try_into().unwrap(),
    50,
    49_u128.into(),
    Err(StarknetError {
        code: StarknetErrorCode::UnknownErrorCode(
            "StarknetErrorCode.GAS_PRICE_TOO_LOW".to_string(),
        ),
        message: "Transaction L2 gas price 49 is below the required threshold 50.".to_string(),
    })
)]
#[case::gas_price_check_disabled_when_percentage_zero_pass(
    100_u128.try_into().unwrap(),
    0,
    0_u128.into(),
    Ok(()),
)]
#[case::tx_gas_price_zero_fails_when_percentage_nonzero_fail(
    100_u128.try_into().unwrap(),
    10,
    0_u128.into(),
    Err(StarknetError {
        code: StarknetErrorCode::UnknownErrorCode(
            "StarknetErrorCode.GAS_PRICE_TOO_LOW".to_string(),
        ),
        message: "Transaction L2 gas price 0 is below the required threshold 10.".to_string(),
    })
)]
#[tokio::test]
```

**File:** crates/apollo_consensus_config/src/config.rs (L47-48)
```rust
    #[validate(range(min = 5, max = 1000))]
    pub far_behind_proposal_threshold: u64,
```

**File:** crates/apollo_consensus_config/src/config_test.rs (L12-24)
```rust
#[test]
fn far_behind_proposal_threshold_must_be_in_range_5_to_1000() {
    // Must be at least 5 and at most 1000 (inclusive bounds).
    for valid in [5, 30, 1000] {
        let config =
            ConsensusDynamicConfig { far_behind_proposal_threshold: valid, ..Default::default() };
        assert!(config.validate().is_ok(), "{valid} should be accepted");
    }
    for invalid in [4, 1001] {
        let config =
            ConsensusDynamicConfig { far_behind_proposal_threshold: invalid, ..Default::default() };
        assert!(config.validate().is_err(), "{invalid} should be rejected");
    }
```

### Title
`min_gas_price_percentage` Lacks Range Validation in `StatefulTransactionValidatorConfig`, Enabling Gateway to Reject All Valid Transactions - (File: `crates/apollo_gateway_config/src/config.rs`)

### Summary
`StatefulTransactionValidatorConfig::min_gas_price_percentage` is a `u8` field (range 0–255) that semantically represents a percentage (0–100). Unlike the analogous `MempoolStaticConfig::fee_escalation_percentage`, which carries `#[validate(range(min = 1, max = 100))]`, `min_gas_price_percentage` has no range constraint. A value above 100 causes the gateway to compute a threshold exceeding the current L2 gas price, silently rejecting every valid V3 transaction at admission.

### Finding Description
In `crates/apollo_gateway_config/src/config.rs`, `StatefulTransactionValidatorConfig` derives `Validate` but `min_gas_price_percentage` carries no `#[validate(range(...))]` annotation:

```rust
#[derive(Clone, Debug, Serialize, Deserialize, Validate, PartialEq)]
pub struct StatefulTransactionValidatorConfig {
    pub validate_resource_bounds: bool,
    ...
    pub min_gas_price_percentage: u8, // E.g., 80 to require 80% of threshold.
}
``` [1](#0-0) 

The field is consumed in `validate_tx_l2_gas_price_within_threshold`:

```rust
let gas_price_threshold_multiplier =
    Ratio::new(self.config.min_gas_price_percentage.into(), 100_u128);
let threshold = (gas_price_threshold_multiplier
    * previous_block_l2_gas_price.get().0)
    .to_integer();
if tx_l2_gas_price.0 < threshold { return Err(...) }
``` [2](#0-1) 

When `min_gas_price_percentage = 150`, the threshold becomes `1.5 × previous_block_l2_gas_price`. Any V3 (`AllResources`) transaction whose `max_price_per_unit` equals the current network gas price (i.e., 100% of the previous block price) is below the threshold and is rejected with `GAS_PRICE_TOO_LOW`.

By contrast, `MempoolStaticConfig::fee_escalation_percentage` — the direct peer field — is properly guarded:

```rust
#[validate(range(min = 1, max = 100))]
pub fee_escalation_percentage: u8,
``` [3](#0-2) 

A test even asserts that `fee_escalation_percentage = 0` fails validation: [4](#0-3) 

No equivalent test or annotation exists for `min_gas_price_percentage`. The config schema publishes the default value of 100 but imposes no upper bound: [5](#0-4) 

### Impact Explanation
When `validate_resource_bounds = true` (the production default) and `min_gas_price_percentage > 100`, every V3 transaction whose offered L2 gas price is at or near the current network price is rejected at the gateway before reaching the mempool. The node effectively stops sequencing user transactions. This matches the allowed impact: **High — Mempool/gateway admission rejects valid transactions before sequencing.**

### Likelihood Explanation
The field is a `u8` (0–255). A misconfigured deployment file, a hot-config push, or a typo (e.g., `150` instead of `15`) silently passes the `Validate` framework because no range constraint exists. The node starts without error, and the misconfiguration is only observable through transaction rejections. The production default is 100, so the window is narrow but real for any operator who tunes the parameter.

### Recommendation
Add a `#[validate(range(min = 0, max = 100))]` annotation to `min_gas_price_percentage` in `StatefulTransactionValidatorConfig`, mirroring the pattern already used for `fee_escalation_percentage` in `MempoolStaticConfig`. Add a corresponding config test asserting that values above 100 fail validation.

```rust
#[validate(range(min = 0, max = 100))]
pub min_gas_price_percentage: u8,
```

### Proof of Concept
1. Set `gateway_config.static_config.stateful_tx_validator_config.min_gas_price_percentage = 150` in the node config JSON.
2. Start the node. No validation error is raised at startup.
3. Submit a V3 `invoke` transaction with `l2_gas.max_price_per_unit` equal to the previous block's L2 gas price (100% of threshold).
4. The gateway computes `threshold = 1.5 × previous_block_l2_gas_price`, finds `tx_price < threshold`, and returns `GAS_PRICE_TOO_LOW`.
5. The transaction is rejected despite being economically valid. The test case `gas_price_check_disabled_when_percentage_zero_pass` in `stateful_transaction_validator_test.rs` already demonstrates the sensitivity of this path to the percentage value. [6](#0-5)

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

**File:** crates/apollo_mempool_config/src/config.rs (L65-66)
```rust
    #[validate(range(min = 1, max = 100))]
    pub fee_escalation_percentage: u8, // E.g., 10 for a 10% increase.
```

**File:** crates/apollo_mempool_config/src/config_test.rs (L10-14)
```rust
#[test]
fn zero_fee_escalation_percentage_fails_validation() {
    let static_config = MempoolStaticConfig { fee_escalation_percentage: 0, ..Default::default() };
    assert!(static_config.validate().is_err());
}
```

**File:** crates/apollo_node/resources/config_schema.json (L3112-3116)
```json
  "gateway_config.static_config.stateful_tx_validator_config.min_gas_price_percentage": {
    "description": "Minimum gas price as percentage of threshold to accept transactions.",
    "privacy": "Public",
    "value": 100
  },
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator_test.rs (L270-286)
```rust
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
```

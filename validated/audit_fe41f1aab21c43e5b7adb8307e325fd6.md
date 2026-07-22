### Title
Missing bounds check on ETH→STRK exchange rate in `ExchangeRateOracleClient` allows incorrect FRI gas price computation - (`crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs`)

### Summary

The `resolve_query` function in `ExchangeRateOracleClient` validates only that the returned ETH→STRK rate is non-zero. No upper or lower bound is enforced. An extreme rate returned by the oracle (e.g., during a market crash analogous to LUNA) propagates unchecked into FRI gas price computation. Because both proposer and validator derive FRI prices from the same oracle, the `within_margin` consensus check does not catch the divergence, and blocks are accepted with economically incorrect L1 gas prices in FRI.

### Finding Description

`resolve_query` in `crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs` (lines 166–213) parses the ETH→STRK rate from the oracle HTTP response and applies exactly two validity checks:

```rust
if rate == 0 {
    return Err(ExchangeRateOracleClientError::InvalidRateError(
        "rate must be non-zero".to_string(),
    ));
}
// ...
if decimals != EXCHANGE_RATE_DECIMALS {
    return Err(...);
}
```

Any non-zero `u128` value passes. There is no configured minimum or maximum bound on the rate.

The rate is then consumed in `get_l1_prices_in_fri_and_wei_and_conversion_rate` (`crates/apollo_consensus_orchestrator/src/utils.rs`, lines 136–222). The WEI prices are clamped to operator-configured `[min, max]` bounds inside `apply_fee_transformations` (lines 273–286):

```rust
price_info.base_fee_per_gas = price_info
    .base_fee_per_gas
    .saturating_add(gas_price_params.l1_gas_tip_wei)
    .clamp(gas_price_params.min_l1_gas_price_wei, gas_price_params.max_l1_gas_price_wei);
```

However, the FRI prices are then derived as `wei_price × rate / 10^18` via `L1PricesInFri::convert_from_wei`. No clamping or bounds check is applied to the resulting FRI values. The `ExchangeRateOracleConfig` struct (`crates/apollo_l1_gas_price_config/src/config.rs`, lines 29–35) contains no `min_rate` or `max_rate` fields.

The `within_margin` check in `is_proposal_init_valid` (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`, lines 342–368) compares the proposer's FRI prices against the validator's own computed FRI prices. Because both nodes query the same oracle and apply the same transformation, they derive identical FRI prices from the same extreme rate. The check passes, and the block is committed with the incorrect prices.

The `ExchangeRateOracleConfig` has no `min_eth_to_fri_rate` or `max_eth_to_fri_rate` fields, and `GasPriceParams` (`crates/apollo_consensus_orchestrator/src/utils.rs`, lines 72–83) likewise carries no rate bounds.

### Impact Explanation

If the oracle returns a rate of `10^12` (instead of the canonical `≈10^21` for 1 ETH ≈ 1000 STRK), the FRI price for L1 gas is:

```
min_l1_gas_price_wei (10^9 wei) × 10^12 / 10^18 = 10^3 FRI
```

The expected FRI price at the correct rate is `10^12 FRI`. The sequencer charges users `10^9×` less than the true L1 cost. Both proposer and validator compute the same `10^3 FRI` value; the `within_margin` (10%) check passes; the block is finalized. Every transaction in the block is undercharged for L1 gas by a factor of `10^9`, constituting a direct economic loss to the sequencer operator. This matches the allowed impact: **Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact.**

### Likelihood Explanation

Moderate. The trigger is an oracle API returning an extreme rate — a realistic scenario during a sharp market dislocation (analogous to the LUNA collapse that motivated the original Chainlink report). No privileged access to the sequencer is required; the oracle endpoint is an external HTTP service whose response content is not bounded by the sequencer code.

### Recommendation

1. Add `min_eth_to_fri_rate: u128` and `max_eth_to_fri_rate: u128` fields to `ExchangeRateOracleConfig` (`crates/apollo_l1_gas_price_config/src/config.rs`).
2. In `resolve_query` (`crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs`, after line 192), reject rates outside these bounds:
   ```rust
   if rate < config.min_eth_to_fri_rate || rate > config.max_eth_to_fri_rate {
       return Err(ExchangeRateOracleClientError::InvalidRateError(
           format!("rate {rate} outside bounds [{}, {}]",
               config.min_eth_to_fri_rate, config.max_eth_to_fri_rate)
       ));
   }
   ```
3. A rejected rate triggers the existing fallback chain (previous block → configured minimum), preventing the extreme value from reaching FRI price computation.

### Proof of Concept

**Setup**: Configure the oracle mock to return `{"price": "0xe8d4a51000", "decimals": 18}` — a rate of `10^12`, nine orders of magnitude below the canonical `10^21`.

**Trace**:

1. `fetch_rate` in `exchange_rate_oracle.rs` (line 221) calls `resolve_query`.
2. `resolve_query` (line 188): `rate = 10^12 ≠ 0` → passes. No upper/lower bound check exists.
3. `get_l1_prices_in_fri_and_wei_and_conversion_rate` (line 159): both `eth_to_fri_rate` and `price_info` are `Ok`.
4. `apply_fee_transformations` (line 165): clamps `base_fee_per_gas` to `min_l1_gas_price_wei = 10^9 wei`.
5. `L1PricesInFri::convert_from_wei` (line 172): `l1_gas_price_fri = 10^9 × 10^12 / 10^18 = 10^3 FRI`.
6. Proposer emits `ProposalInit` with `l1_gas_price_fri = 10^3`.
7. Validator calls `is_proposal_init_valid` (line 253): queries same oracle, computes same `10^3 FRI` reference.
8. `within_margin(10^3, 10^3, 10)` (line 342): `abs_diff = 0 ≤ 1` → returns `true`.
9. Block is accepted. Every transaction is charged `10^9×` less than the true L1 cost. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs (L166-213)
```rust
fn resolve_query(
    body: String,
    metrics: &ExchangeRateOracleMetrics,
) -> Result<u128, ExchangeRateOracleClientError> {
    let Ok(json): Result<serde_json::Value, _> = serde_json::from_str(&body) else {
        return Err(ExchangeRateOracleClientError::ParseError(format!(
            "Failed to parse JSON: {body}"
        )));
    };
    // Extract price from API response. Also returns MissingFieldError if value is not a string.
    let price = match json.get("price").and_then(|v| v.as_str()) {
        Some(price) => price,
        None => {
            return Err(ExchangeRateOracleClientError::MissingFieldError(
                "price".to_string(),
                body,
            ));
        }
    };
    let rate = u128::from_str_radix(price.trim_start_matches("0x"), 16).map_err(|e| {
        ExchangeRateOracleClientError::ParseError(format!("Failed to parse price {price}: {e}"))
    })?;
    if rate == 0 {
        return Err(ExchangeRateOracleClientError::InvalidRateError(
            "rate must be non-zero".to_string(),
        ));
    }
    // Extract decimals from API response. Also returns MissingFieldError if value is not a number.
    let decimals = match json.get("decimals").and_then(|v| v.as_u64()) {
        Some(decimals) => decimals,
        None => {
            return Err(ExchangeRateOracleClientError::MissingFieldError(
                "decimals".to_string(),
                body,
            ));
        }
    };
    if decimals != EXCHANGE_RATE_DECIMALS {
        return Err(ExchangeRateOracleClientError::InvalidDecimalsError(
            EXCHANGE_RATE_DECIMALS,
            decimals,
        ));
    }
    metrics.success_count.increment(1);
    set_unix_now_seconds(metrics.last_success_timestamp);
    metrics.rate.set_lossy(rate);
    Ok(rate)
}
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L72-83)
```rust
#[derive(Debug)]
pub(crate) struct GasPriceParams {
    pub min_l1_gas_price_wei: GasPrice,
    pub max_l1_gas_price_wei: GasPrice,
    pub max_l1_data_gas_price_wei: GasPrice,
    pub min_l1_data_gas_price_wei: GasPrice,
    pub l1_data_gas_price_multiplier: Ratio<u128>,
    pub l1_gas_tip_wei: GasPrice,
    pub override_l1_gas_price_fri: Option<GasPrice>,
    pub override_l1_data_gas_price_fri: Option<GasPrice>,
    pub override_eth_to_fri_rate: Option<u128>,
}
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L159-182)
```rust
    if let (Ok(eth_to_fri_rate), Ok(mut price_info)) = (eth_to_fri_rate, price_info) {
        // Both L1 prices and rate are Ok, so we can use them.
        info!(
            "raw eth_to_fri_rate (from oracle): {eth_to_fri_rate}, raw l1 gas price wei (from \
             provider): {price_info:?}"
        );
        apply_fee_transformations(&mut price_info, gas_price_params);
        let prices_in_wei = L1PricesInWei {
            l1_gas_price: price_info.base_fee_per_gas,
            l1_data_gas_price: price_info.blob_fee,
        };
        // Apply the eth/strk rate to get prices in fri.
        let l1_gas_prices_fri_result =
            L1PricesInFri::convert_from_wei(&prices_in_wei, eth_to_fri_rate);
        // If conversion fails, leave return_value=None to try backup methods.
        if let Ok(prices_in_fri) = l1_gas_prices_fri_result {
            return (prices_in_fri, prices_in_wei, eth_to_fri_rate);
        } else {
            warn!(
                "Failed to convert L1 gas prices to FRI: {:?}",
                l1_gas_prices_fri_result.clone().err()
            );
        }
    }
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L273-286)
```rust
pub(crate) fn apply_fee_transformations(
    price_info: &mut PriceInfo,
    gas_price_params: &GasPriceParams,
) {
    price_info.base_fee_per_gas = price_info
        .base_fee_per_gas
        .saturating_add(gas_price_params.l1_gas_tip_wei)
        .clamp(gas_price_params.min_l1_gas_price_wei, gas_price_params.max_l1_gas_price_wei);

    price_info.blob_fee = GasPrice(
        (gas_price_params.l1_data_gas_price_multiplier * price_info.blob_fee.0).to_integer(),
    )
    .clamp(gas_price_params.min_l1_data_gas_price_wei, gas_price_params.max_l1_data_gas_price_wei);
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L342-368)
```rust
    if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, l1_gas_price_margin_percent)
        && within_margin(
            l1_data_gas_price_fri_proposed,
            l1_data_gas_price_fri,
            l1_gas_price_margin_percent,
        )
        && within_margin(l1_gas_price_wei_proposed, l1_gas_price_wei, l1_gas_price_margin_percent)
        && within_margin(
            l1_data_gas_price_wei_proposed,
            l1_data_gas_price_wei,
            l1_gas_price_margin_percent,
        ))
    {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "L1 gas price mismatch: expected L1 gas price FRI={l1_gas_price_fri}, \
                 proposed={l1_gas_price_fri_proposed}, expected L1 data gas price \
                 FRI={l1_data_gas_price_fri}, proposed={l1_data_gas_price_fri_proposed}, expected \
                 L1 gas price WEI={l1_gas_price_wei}, proposed={l1_gas_price_wei_proposed}, \
                 expected L1 data gas price WEI={l1_data_gas_price_wei}, \
                 proposed={l1_data_gas_price_wei_proposed}, \
                 l1_gas_price_margin_percent={l1_gas_price_margin_percent}"
            ),
        ));
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L427-438)
```rust
fn within_margin(proposed: GasPrice, reference: GasPrice, margin_percent: u128) -> bool {
    // For small numbers (e.g., less than 10 wei, if margin is 10%), even an off-by-one
    // error might be bigger than the margin, even if it is just a rounding error.
    // We make an exception for such mismatch, and don't bother checking percentages
    // if the difference in price is only one wei.
    if proposed.0.abs_diff(reference.0) <= GAS_PRICE_ABS_DIFF_MARGIN {
        return true;
    }
    // Saturate: `reference.0 * margin_percent` can overflow u128 on large WEI prices.
    let margin = reference.0.saturating_mul(margin_percent) / 100;
    proposed.0.abs_diff(reference.0) <= margin
}
```

**File:** crates/apollo_l1_gas_price_config/src/config.rs (L29-35)
```rust
pub struct ExchangeRateOracleConfig {
    #[serde(deserialize_with = "deserialize_optional_sensitive_list_with_url_and_headers")]
    pub url_header_list: Option<Vec<Sensitive<UrlAndHeaders>>>,
    pub lag_interval_seconds: u64,
    pub max_cache_size: usize,
    pub query_timeout_sec: u64,
}
```

### Title
`resolve_query` in `ExchangeRateOracleClient` accepts oracle responses without validating the response `timestamp` field, allowing stale ETH→STRK rates to be used for L1 gas price conversion - (`File: crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs`)

---

### Summary

`ExchangeRateOracleClient::fetch_rate` sends a request to the oracle API with a specific `adjusted_timestamp` query parameter, but `resolve_query` — the function that parses the response body — only reads `price` and `decimals`. It never reads or validates the `timestamp` field that the oracle API includes in its response. If the oracle returns data for a different (older) time bucket than requested, the sequencer silently accepts and caches the stale rate, using it to convert L1 gas prices from WEI to FRI in block proposals.

---

### Finding Description

In `fetch_rate`, the client computes a `quantized_timestamp` and a corresponding `adjusted_timestamp`, then fires an HTTP GET request with `?timestamp=adjusted_timestamp`:

```
url.query_pairs_mut().append_pair("timestamp", &adjusted_timestamp.to_string());
```

The oracle API is expected to return a JSON body that includes a `timestamp` field alongside `price` and `decimals` (confirmed by the integration-test mock):

```rust
// crates/apollo_integration_tests/src/utils.rs:646
let response = json!({ "timestamp": query.timestamp, "price": price, "decimals": EXCHANGE_RATE_DECIMALS });
```

However, `resolve_query` only reads and validates `price` and `decimals`:

```rust
// crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs:176-212
let price = match json.get("price").and_then(|v| v.as_str()) { ... };
let decimals = match json.get("decimals").and_then(|v| v.as_u64()) { ... };
// No check on json.get("timestamp")
```

The `timestamp` field in the response body is never read. If the oracle returns a response whose `timestamp` does not match the requested `adjusted_timestamp` (e.g., due to oracle-side caching, fallback to stale data, or a transient fault), the sequencer accepts the rate, caches it under `quantized_timestamp`, and uses it for ETH→STRK conversion.

This is structurally identical to the external report: `getRate` reads `answer` but never checks `updatedAt`; here `resolve_query` reads `price` but never checks `timestamp`.

The stale rate then flows through:

1. `L1GasPriceProvider::eth_to_fri_rate` → `fetch_rate` → stale `u128` rate
2. `get_l1_prices_in_fri_and_wei_and_conversion_rate` → `L1PricesInFri::convert_from_wei(&prices_in_wei, eth_to_fri_rate)`
3. The wrong FRI prices are embedded in the `ProposalInit` sent to consensus

Note the contrast with `get_price_info`, which **does** perform a staleness check:

```rust
// crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs:137-142
if timestamp.0 > (*last_timestamp + self.config.max_time_gap_seconds) {
    return Err(L1GasPriceProviderError::StaleL1GasPricesError { ... });
}
```

No equivalent guard exists for the oracle exchange-rate path.

---

### Impact Explanation

A stale ETH→STRK rate causes `L1PricesInFri` to be computed incorrectly. If STRK has appreciated since the stale rate was recorded, FRI prices are too low — the sequencer under-charges users for L1 gas costs, creating a direct economic loss. If STRK has depreciated, users are overcharged. Because the validator's margin check (`l1_gas_price_margin_percent`) is applied against the validator's own (also potentially stale) oracle query, a stale rate that falls within the margin passes consensus silently, embedding wrong fee accounting into finalized blocks.

This matches: **Critical. Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact.**

---

### Likelihood Explanation

The oracle is an external HTTP API. Oracle-side caching, fallback logic, or transient faults can cause the API to return a response body whose `timestamp` field does not match the requested `adjusted_timestamp`. No attacker privilege is required — a legitimate oracle experiencing downtime or serving cached data is sufficient. The `ExchangeRateOracleConfig` has no `max_staleness` parameter, so there is no operator-configurable defense.

---

### Recommendation

In `resolve_query`, read and validate the `timestamp` field from the response body against the `adjusted_timestamp` that was requested. Pass `adjusted_timestamp` into `resolve_query` and reject responses where the returned timestamp deviates beyond a configurable tolerance:

```rust
fn resolve_query(
    body: String,
    metrics: &ExchangeRateOracleMetrics,
    expected_timestamp: u64,          // <-- add
    max_timestamp_deviation: u64,     // <-- add (from config)
) -> Result<u128, ExchangeRateOracleClientError> {
    // ... existing price/decimals parsing ...

    // NEW: validate response timestamp
    let response_ts = json.get("timestamp").and_then(|v| v.as_u64())
        .ok_or_else(|| ExchangeRateOracleClientError::MissingFieldError("timestamp".into(), body.clone()))?;
    if response_ts.abs_diff(expected_timestamp) > max_timestamp_deviation {
        return Err(ExchangeRateOracleClientError::StaleRateError {
            expected: expected_timestamp,
            got: response_ts,
        });
    }
    // ...
}
```

Additionally, add a `max_oracle_timestamp_deviation_seconds` field to `ExchangeRateOracleConfig` analogous to `max_time_gap_seconds` in `L1GasPriceProviderConfig`.

---

### Proof of Concept

1. Configure the sequencer with an oracle URL pointing to a server that always returns a fixed stale timestamp (e.g., `"timestamp": 0`) regardless of the `?timestamp=` query parameter, but with a valid `price` and `decimals: 18`.
2. Call `fetch_rate(current_timestamp)`. `resolve_query` parses `price` and `decimals`, ignores `timestamp: 0`, and returns the rate successfully.
3. The stale rate is cached under `quantized_timestamp` and returned to `get_l1_prices_in_fri_and_wei_and_conversion_rate`.
4. The proposer embeds wrong FRI gas prices in the `ProposalInit`, which validators accept if the deviation is within `l1_gas_price_margin_percent`.

The integration-test mock at [1](#0-0)  confirms the oracle API returns a `timestamp` field. The `resolve_query` function at [2](#0-1)  reads only `price` and `decimals`, never `timestamp`. The stale rate flows into `get_l1_prices_in_fri_and_wei_and_conversion_rate` at [3](#0-2)  and is used to compute `L1PricesInFri`. The contrast with the existing staleness guard in `get_price_info` is at [4](#0-3) . The `ExchangeRateOracleConfig` at [5](#0-4)  has no staleness bound for the oracle response timestamp.

### Citations

**File:** crates/apollo_integration_tests/src/utils.rs (L644-647)
```rust
    let price = format!("0x{DEFAULT_ETH_TO_FRI_RATE:x}");
    let response =
        json!({ "timestamp": query.timestamp ,"price": price, "decimals": EXCHANGE_RATE_DECIMALS });
    Json(response)
```

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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L148-175)
```rust
    let (eth_to_fri_rate, price_info) = tokio::join!(
        l1_gas_price_provider_client.get_rate(timestamp),
        l1_gas_price_provider_client.get_price_info(BlockTimestamp(timestamp))
    );
    if price_info.is_err() {
        warn!("Failed to get l1 gas price from provider: {:?}", price_info);
        CONSENSUS_L1_GAS_PRICE_PROVIDER_ERROR.increment(1);
    }
    if eth_to_fri_rate.is_err() {
        warn!("Failed to get eth to fri rate from oracle: {:?}", eth_to_fri_rate);
    }
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
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L136-142)
```rust
        // Check if the prices are stale.
        if timestamp.0 > (*last_timestamp + self.config.max_time_gap_seconds) {
            return Err(L1GasPriceProviderError::StaleL1GasPricesError {
                current_timestamp: timestamp.0,
                last_valid_price_timestamp: *last_timestamp,
            });
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

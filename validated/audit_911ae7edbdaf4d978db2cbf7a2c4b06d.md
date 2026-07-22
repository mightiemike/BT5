### Title
Missing Staleness Check for ETH→STRK Oracle Rate When Combined with L1 Gas Price in `get_l1_prices_in_fri_and_wei_and_conversion_rate` — (`crates/apollo_consensus_orchestrator/src/utils.rs`)

---

### Summary

The sequencer combines two independent data sources to compute the L1 gas price in FRI: the L1 gas price provider (`get_price_info`) and the ETH→STRK exchange-rate oracle (`get_rate`). The L1 gas price provider enforces an explicit staleness bound (`max_time_gap_seconds`), but the ETH→STRK oracle has no equivalent check. The oracle's fallback path can silently return a cached rate from the previous quantized time bucket — up to `2 × lag_interval_seconds` old — while the L1 gas price data is fresh. The combined FRI price is therefore computed from two sources with asymmetric freshness guarantees, exactly mirroring the external report's pattern.

---

### Finding Description

In `get_l1_prices_in_fri_and_wei_and_conversion_rate` (`crates/apollo_consensus_orchestrator/src/utils.rs`, lines 148–151), both sources are fetched concurrently:

```rust
let (eth_to_fri_rate, price_info) = tokio::join!(
    l1_gas_price_provider_client.get_rate(timestamp),
    l1_gas_price_provider_client.get_price_info(BlockTimestamp(timestamp))
);
```

**Source 1 — L1 gas price (`get_price_info`):** Inside `L1GasPriceProvider::get_price_info` (`crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs`, lines 136–142), an explicit staleness gate is enforced:

```rust
if timestamp.0 > (*last_timestamp + self.config.max_time_gap_seconds) {
    return Err(L1GasPriceProviderError::StaleL1GasPricesError { ... });
}
```

With the production default `max_time_gap_seconds = 900`, any L1 gas price data older than 15 minutes is rejected.

**Source 2 — ETH→STRK rate (`get_rate` → `fetch_rate`):** Inside `ExchangeRateOracleClient::fetch_rate` (`crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs`, lines 221–278), there is **no equivalent staleness check**. The function quantizes the timestamp and checks an LRU cache:

```rust
let quantized_timestamp = (timestamp - self.config.lag_interval_seconds)
    .checked_div(self.config.lag_interval_seconds)
    .expect("lag_interval_seconds should be non-zero");

if let Some(rate) = cache.get(&quantized_timestamp) {
    return Ok(*rate);   // ← no age check on the cached entry
}
...
if !handle.is_finished() {
    if let Some(rate) = cache.get(&(quantized_timestamp - NUMBER_OF_TIMESTAMPS_BACK)) {
        return Ok(*rate);   // ← fallback: previous bucket, up to lag_interval_seconds older
    }
    return Err(ExchangeRateOracleClientError::QueryNotReadyError(timestamp));
}
```

The fallback path returns the rate from `quantized_timestamp - 1`, which corresponds to a rate that was fetched up to `lag_interval_seconds` seconds before the current bucket started. Because the current bucket itself spans `lag_interval_seconds` seconds, the returned rate can be up to `2 × lag_interval_seconds` seconds old. With the production default `lag_interval_seconds = 900`, the oracle can silently return a rate up to **1800 seconds (30 minutes) old** — double the L1 gas price staleness bound.

When both sources succeed (the `if let (Ok(eth_to_fri_rate), Ok(mut price_info)) = ...` branch at line 159), the stale oracle rate is multiplied against the fresh L1 WEI price to produce the FRI price:

```rust
let l1_gas_prices_fri_result =
    L1PricesInFri::convert_from_wei(&prices_in_wei, eth_to_fri_rate);
```

No staleness check is applied to `eth_to_fri_rate` before this multiplication.

---

### Impact Explanation

The FRI-denominated L1 gas prices (`l1_gas_price_fri`, `l1_data_gas_price_fri`) are embedded in every block proposal's `ProposalInit` and are validated by every consensus participant in `is_proposal_init_valid` (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`, lines 342–367). A stale ETH→STRK rate produces wrong FRI prices. If the stale rate diverges enough from the current market rate, the proposer's FRI prices will fall outside the validator's `l1_gas_price_margin_percent` window, causing valid proposals to be rejected (liveness degradation). If the divergence is within the margin, the accepted block carries incorrect fee accounting — users are over- or under-charged for L1 gas costs — matching the "Incorrect fee, gas, bouncer, resource accounting" critical impact category and the "RPC execution, fee estimation … returns an authoritative-looking wrong value" high impact category.

---

### Likelihood Explanation

The fallback path is exercised every time the oracle's in-flight query for the current time bucket has not yet resolved when a block is proposed. With `lag_interval_seconds = 900` and block times of seconds, the first call in each 15-minute bucket always hits this path. The asymmetry between `max_time_gap_seconds = 900` and the oracle's effective maximum staleness of `2 × 900 = 1800` seconds is present in the default production configuration (`crates/apollo_deployments/resources/app_configs/l1_gas_price_provider_config.json`). No privileged access is required; the condition is triggered by normal block production timing.

---

### Recommendation

Add an explicit staleness check inside `fetch_rate` (or at the call site in `get_l1_prices_in_fri_and_wei_and_conversion_rate`) that rejects a cached oracle rate whose age exceeds `max_time_gap_seconds`. Concretely:

1. Store the wall-clock time alongside each cached rate in `ExchangeRateOracleClient`.
2. Before returning a cached entry (both the direct hit and the `quantized_timestamp - 1` fallback), verify that `now - cached_time <= max_time_gap_seconds`.
3. If the check fails, return `ExchangeRateOracleClientError::QueryNotReadyError` so the caller falls through to the previous-block-info or default fallback, consistent with how `get_price_info` handles stale L1 data.

Alternatively, pass `max_time_gap_seconds` into `ExchangeRateOracleConfig` and enforce it symmetrically with the L1 gas price provider's staleness gate.

---

### Proof of Concept

**Scenario:**

1. At time `T = 0`, the oracle resolves and caches `rate_A` for `quantized_timestamp = Q`.
2. At time `T = 900` (start of bucket `Q+1`), the oracle spawns a new query for `Q+1` but it has not yet resolved.
3. At time `T = 1799` (still in bucket `Q+1`), a block is proposed.
   - `fetch_rate` computes `quantized_timestamp = Q+1`, misses the cache, finds the in-flight query unfinished, falls back to `Q` (previous bucket), and returns `rate_A` — now **1799 seconds old**.
   - `get_price_info` succeeds because the L1 scraper is current (within 900 seconds).
   - `get_l1_prices_in_fri_and_wei_and_conversion_rate` enters the `(Ok, Ok)` branch and computes `l1_gas_price_fri = l1_gas_price_wei × rate_A / WEI_PER_ETH`.
4. If the true ETH→STRK rate has moved significantly in the past 1799 seconds, the FRI price is wrong. Validators compute their own FRI price using a fresh oracle rate and may reject the proposal or accept a block with incorrect fee accounting.

**Relevant code locations:**

- Staleness check present for L1 gas price: [1](#0-0) 
- No staleness check for oracle rate (fallback path): [2](#0-1) 
- Combined usage without oracle staleness gate: [3](#0-2) 
- Validator checks FRI prices from the combined result: [4](#0-3) 
- Production config showing asymmetric defaults: [5](#0-4)

### Citations

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

**File:** crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs (L239-252)
```rust
        if !handle.is_finished() {
            debug!("Query not yet resolved: timestamp={timestamp}");
            // If the previous quantized timestamp is in the cache, use it.
            if let Some(rate) = cache.get(&(quantized_timestamp - NUMBER_OF_TIMESTAMPS_BACK)) {
                debug!(
                    "Query not yet resolved: timestamp={timestamp}, using previous rate {rate} \
                     from quantized timestamp={}",
                    (quantized_timestamp - NUMBER_OF_TIMESTAMPS_BACK)
                        * self.config.lag_interval_seconds
                );
                return Ok(*rate);
            }
            // If not, return a query not ready error.
            return Err(ExchangeRateOracleClientError::QueryNotReadyError(timestamp));
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L342-367)
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
```

**File:** crates/apollo_deployments/resources/app_configs/l1_gas_price_provider_config.json (L1-12)
```json
{
  "l1_gas_price_provider_config.eth_to_strk_oracle_config.lag_interval_seconds": 900,
  "l1_gas_price_provider_config.eth_to_strk_oracle_config.max_cache_size": 100,
  "l1_gas_price_provider_config.eth_to_strk_oracle_config.query_timeout_sec": 10,
  "l1_gas_price_provider_config.strk_to_usd_oracle_config.lag_interval_seconds": 900,
  "l1_gas_price_provider_config.strk_to_usd_oracle_config.max_cache_size": 100,
  "l1_gas_price_provider_config.strk_to_usd_oracle_config.query_timeout_sec": 10,
  "l1_gas_price_provider_config.lag_margin_seconds": 600,
  "l1_gas_price_provider_config.number_of_blocks_for_mean": 300,
  "l1_gas_price_provider_config.storage_limit": 3000,
  "l1_gas_price_provider_config.max_time_gap_seconds": 900
}
```

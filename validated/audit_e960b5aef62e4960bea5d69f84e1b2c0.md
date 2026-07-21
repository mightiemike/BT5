### Title
Unchecked `u64` subtraction underflow in `ExchangeRateOracleClient::fetch_rate` corrupts oracle timestamp quantization and injects wrong ETH→STRK rate into block gas-price accounting - (File: `crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs`)

---

### Summary

`ExchangeRateOracleClient::fetch_rate` performs two bare `u64` subtractions without overflow protection. When the caller-supplied `timestamp` is smaller than `lag_interval_seconds`, the first subtraction wraps to a near-`u64::MAX` value, producing a completely wrong `quantized_timestamp`. A second bare subtraction on the same variable underflows when `quantized_timestamp == 0`. Both paths feed directly into the ETH→STRK conversion rate used to compute L1 gas prices that are committed into every block's `BlockInfo`.

---

### Finding Description

**First underflow — line 223:**

```rust
// crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs  L221-225
async fn fetch_rate(&self, timestamp: u64) -> Result<u128, ExchangeRateOracleClientError> {
    const NUMBER_OF_TIMESTAMPS_BACK: u64 = 1;
    let quantized_timestamp = (timestamp - self.config.lag_interval_seconds)  // ← bare u64 sub
        .checked_div(self.config.lag_interval_seconds)
        .expect("lag_interval_seconds should be non-zero");
```

`timestamp` and `lag_interval_seconds` are both `u64`. When `timestamp < lag_interval_seconds` the subtraction wraps (release mode) or panics (debug / `overflow-checks = true`). The wrapped result is divided by `lag_interval_seconds` to produce a `quantized_timestamp` near `u64::MAX / lag_interval_seconds`.

**Second underflow — line 242:**

```rust
// L242
if let Some(rate) = cache.get(&(quantized_timestamp - NUMBER_OF_TIMESTAMPS_BACK)) {
```

`NUMBER_OF_TIMESTAMPS_BACK = 1`. When `quantized_timestamp == 0` (reachable when `timestamp == lag_interval_seconds`, the smallest non-underflowing input), this second subtraction also wraps to `u64::MAX`.

**Call path to block execution:**

`fetch_rate` ← `L1GasPriceProvider::eth_to_fri_rate` ← `get_l1_prices_in_fri_and_wei_and_conversion_rate` ← `get_l1_prices_in_fri_and_wei` ← both `initiate_build` (proposer) and `is_proposal_init_valid` (validator). [1](#0-0) 

The corrupted `quantized_timestamp` is then passed to `spawn_query`, which multiplies it by `lag_interval_seconds` to form `adjusted_timestamp`:

```rust
// L107
let adjusted_timestamp = quantized_timestamp * self.config.lag_interval_seconds;
```

This multiplication itself can overflow `u64` when `quantized_timestamp` is near `u64::MAX`, producing a second wrong value that is sent as the `timestamp` query parameter to the external oracle URL. [2](#0-1) 

The oracle rejects or mishandles the astronomically large timestamp. The error propagates back through `get_l1_prices_in_fri_and_wei_and_conversion_rate`, which falls back to previous-block prices or the hardcoded `DEFAULT_ETH_TO_FRI_RATE`: [3](#0-2) 

The fallback rate is then used to compute `l1_gas_price_fri` / `l1_data_gas_price_fri` that are placed into `ProposalInit` and ultimately into `BlockInfo` passed to the blockifier for fee accounting. [4](#0-3) 

---

### Impact Explanation

**Panic / DoS (debug builds or `overflow-checks = true`):** The bare subtraction panics inside the validator's `is_proposal_init_valid` call. The validator task terminates, the proposal is dropped, and consensus stalls for that round. Any proposer that sends a `ProposalInit` with `timestamp < lag_interval_seconds` (e.g., `timestamp = 0` at genesis when `last_block_timestamp = 0`) can trigger this.

**Wrong gas prices committed to block (release builds, wrapping):** The underflow silently produces a wrong `quantized_timestamp`. The oracle query fails (wrong timestamp), the fallback `DEFAULT_ETH_TO_FRI_RATE` is used, and the resulting `l1_gas_price_fri` / `l1_data_gas_price_fri` values are committed into the block's `BlockInfo`. These wrong prices affect every transaction's fee calculation in that block — matching the "Incorrect fee, gas, bouncer, resource accounting … with economic impact" impact category. [5](#0-4) 

---

### Likelihood Explanation

The trigger condition `timestamp < lag_interval_seconds` is reachable in two concrete scenarios:

1. **Genesis / first block:** `last_block_timestamp = 0`, so `is_proposal_init_valid` accepts any `timestamp >= 0`. A proposer can legitimately (or maliciously) set `timestamp = 0`. With the default `lag_interval_seconds = 1`, `0 - 1` underflows.

2. **Larger `lag_interval_seconds` configuration:** If an operator sets `lag_interval_seconds` to e.g. 3600 (hourly buckets), any proposal timestamp below 3600 triggers the underflow. This is a valid operator configuration.

The `timestamp` field in `ProposalInit` is network-received and only weakly validated (must be `>= last_block_timestamp` and `<= now + window`): [6](#0-5) 

---

### Recommendation

Replace both bare subtractions with checked or saturating arithmetic:

```rust
// Line 223 — use checked_sub and return an error instead of wrapping
let quantized_timestamp = timestamp
    .checked_sub(self.config.lag_interval_seconds)
    .ok_or(ExchangeRateOracleClientError::InvalidTimestamp(timestamp))?
    .checked_div(self.config.lag_interval_seconds)
    .expect("lag_interval_seconds should be non-zero");

// Line 242 — use saturating_sub or guard with an explicit check
if quantized_timestamp > 0 {
    if let Some(rate) = cache.get(&(quantized_timestamp - NUMBER_OF_TIMESTAMPS_BACK)) {
        ...
    }
}
```

Additionally, validate that `timestamp >= lag_interval_seconds` at the call sites in `get_l1_prices_in_fri_and_wei_and_conversion_rate` before invoking `fetch_rate`, analogous to how the original oracle recommendation required verifying `latestTimestamp` is within accepted bounds.

---

### Proof of Concept

```rust
// Reproduces the underflow in fetch_rate with timestamp=0, lag_interval_seconds=1 (default)
#[tokio::test]
async fn underflow_at_genesis_timestamp() {
    use apollo_l1_gas_price::exchange_rate_oracle::ExchangeRateOracleClient;
    use apollo_l1_gas_price_config::config::ExchangeRateOracleConfig;
    use apollo_l1_gas_price_types::ExchangeRateOracleClientTrait;

    let config = ExchangeRateOracleConfig {
        lag_interval_seconds: 1,  // default
        ..Default::default()
    };
    let client = ExchangeRateOracleClient::new(config, /* metrics */);

    // timestamp=0 < lag_interval_seconds=1 → underflow on line 223
    // In debug mode: panics with "attempt to subtract with overflow"
    // In release mode (wrapping): quantized_timestamp = u64::MAX
    //   → spawn_query(u64::MAX) → adjusted_timestamp = u64::MAX * 1 = u64::MAX
    //   → oracle query sent with timestamp=18446744073709551615
    //   → oracle returns error → fallback DEFAULT_ETH_TO_FRI_RATE used
    let _ = client.fetch_rate(0).await;
}
```

The same trigger is reachable from the consensus validator path: a proposer at genesis sends `ProposalInit { timestamp: 0, .. }`, which passes the timestamp range check (since `last_block_timestamp = 0`), then reaches `fetch_rate(0)` inside `is_proposal_init_valid`. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs (L107-107)
```rust
        let adjusted_timestamp = quantized_timestamp * self.config.lag_interval_seconds;
```

**File:** crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs (L221-253)
```rust
    async fn fetch_rate(&self, timestamp: u64) -> Result<u128, ExchangeRateOracleClientError> {
        const NUMBER_OF_TIMESTAMPS_BACK: u64 = 1;
        let quantized_timestamp = (timestamp - self.config.lag_interval_seconds)
            .checked_div(self.config.lag_interval_seconds)
            .expect("lag_interval_seconds should be non-zero");

        let mut cache = self.cached_prices.lock().unwrap();

        if let Some(rate) = cache.get(&quantized_timestamp) {
            debug!("Cached conversion rate for timestamp {timestamp} is {rate}");
            return Ok(*rate);
        }

        // Check if there is a query already sent out for this timestamp, if not, start one.
        let mut queries = self.queries.lock().unwrap();
        let handle = queries
            .get_or_insert_mut(quantized_timestamp, || self.spawn_query(quantized_timestamp));
        // If the query is not finished, return an error.
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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L301-348)
```rust
pub(crate) fn convert_to_sn_api_block_info(
    init: &ProposalInit,
) -> Result<starknet_api::block::BlockInfo, StarknetApiError> {
    if init.l1_gas_price_fri.0 == 0
        || init.l1_gas_price_wei.0 == 0
        || init.l1_data_gas_price_fri.0 == 0
        || init.l1_data_gas_price_wei.0 == 0
        || init.l2_gas_price_fri.0 == 0
    {
        warn!("Zero gas price detected in block info: {:?}", init);
    }

    let l1_gas_price_fri = NonzeroGasPrice::new(init.l1_gas_price_fri)?;
    let l1_data_gas_price_fri = NonzeroGasPrice::new(init.l1_data_gas_price_fri)?;
    let l1_gas_price_wei = NonzeroGasPrice::new(init.l1_gas_price_wei)?;
    let l1_data_gas_price_wei = NonzeroGasPrice::new(init.l1_data_gas_price_wei)?;
    let l2_gas_price_fri = NonzeroGasPrice::new(init.l2_gas_price_fri)?;
    let proposal_init_info = PreviousProposalInitInfo::from(init);
    let eth_to_fri_rate = calculate_eth_to_fri_rate(&proposal_init_info)?;

    let l2_gas_price_wei = NonzeroGasPrice::new(init.l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)?)
        .inspect_err(|_| {
            warn!(
                "L2 gas price in wei is zero! Conversion rate: {eth_to_fri_rate}, L2 gas price in \
                 FRI: {}",
                init.l2_gas_price_fri
            )
        })?;
    Ok(starknet_api::block::BlockInfo {
        block_number: init.height,
        block_timestamp: BlockTimestamp(init.timestamp),
        sequencer_address: init.builder,
        gas_prices: GasPrices {
            strk_gas_prices: GasPriceVector {
                l1_gas_price: l1_gas_price_fri,
                l1_data_gas_price: l1_data_gas_price_fri,
                l2_gas_price: l2_gas_price_fri,
            },
            eth_gas_prices: GasPriceVector {
                l1_gas_price: l1_gas_price_wei,
                l1_data_gas_price: l1_data_gas_price_wei,
                l2_gas_price: l2_gas_price_wei,
            },
        },
        use_kzg_da: init.l1_da_mode.is_use_kzg_da(),
        starknet_version: init.starknet_version,
    })
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L260-285)
```rust
    let now: u64 = clock.unix_now();
    let last_block_timestamp =
        proposal_init_validation.previous_proposal_init.as_ref().map_or(0, |info| info.timestamp);
    if init_proposed.timestamp < last_block_timestamp {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "Timestamp is too old: last_block_timestamp={}, proposed={}",
                last_block_timestamp, init_proposed.timestamp
            ),
        ));
    }
    if init_proposed.timestamp > now + proposal_init_validation.block_timestamp_window_seconds {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "Timestamp is in the future: now={}, block_timestamp_window_seconds={}, \
                 proposed={}",
                now,
                proposal_init_validation.block_timestamp_window_seconds,
                init_proposed.timestamp
            ),
        ));
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L322-328)
```rust
    let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
        l1_gas_price_provider,
        init_proposed.timestamp,
        proposal_init_validation.previous_proposal_init.as_ref(),
        gas_price_params,
    )
    .await;
```

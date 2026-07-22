### Title
Single Compromised Oracle URL in `ExchangeRateOracleClient` Drives `fee_proposal` to Extremes via First-Success Selection Without Cross-Validation — (`File: crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs`)

---

### Summary

`ExchangeRateOracleClient::spawn_query()` iterates the configured `url_header_list` and returns the **first** URL that responds successfully, with no cross-validation against the other sources. A single compromised oracle endpoint that returns a syntactically valid but manipulated `strk_usd_rate` (non-zero, correct decimals) is accepted unconditionally. This rate feeds directly into `compute_fee_target`, which computes `fee_target = target_atto_usd_per_l2_gas × 10^18 / strk_usd_rate`. A near-zero rate produces `fee_target ≈ u128::MAX`, which `compute_fee_proposal` clamps to `fee_actual × (1 + 2/1000)` — the maximum allowed per block. The resulting `fee_proposal` is committed into the `ProposalCommitment` hash that consensus signs over, and is stored in `fee_proposals_window` to compute `fee_actual` for future blocks. Over many blocks, this compounds the L2 gas price to extremes.

---

### Finding Description

**Root cause — `spawn_query` first-success selection:** [1](#0-0) 

The loop iterates `url_header_list` starting from the stored `index` and returns `Ok(rate)` on the **first** URL that responds with HTTP 200 and a parseable body. There is no comparison between multiple sources, no median, no outlier detection, and no sanity bound on the returned rate. The only rejection criteria are: `rate == 0` and `decimals != 18`. [2](#0-1) 

**Propagation — rate flows into `compute_fee_target`:** [3](#0-2) 

`resolve_fee_target` calls `compute_fee_target(target_atto_usd_per_l2_gas, rate)`. With a manipulated `strk_usd_rate = 1` (minimum non-zero), the formula `target × 10^18 / 1` overflows `u128` and saturates to `u128::MAX`: [4](#0-3) 

**Clamping — `compute_fee_proposal` clamps to upper bound every block:** [5](#0-4) 

With `fee_proposal_margin_ppt = 2` (0.2% per block, all versions V0_14_0–V0_14_4): [6](#0-5) 

The proposer always emits `fee_proposal = fee_actual × 1.002`. The validator accepts this because it is within bounds: [7](#0-6) 

**Commitment — wrong `fee_proposal` is hashed into `ProposalCommitment`:** [8](#0-7) 

From Starknet V0_14_3 onward, `ProposalCommitment = Poseidon(partial_block_hash, fee_proposal_fri)`. The manipulated `fee_proposal` is thus bound into the commitment that consensus signs over, and stored in `fee_proposals_window` to compute `fee_actual` for future blocks via the median: [9](#0-8) 

---

### Impact Explanation

**Impact: Critical — Incorrect fee/gas accounting with economic impact.**

With `fee_proposal_margin_ppt = 2` and `fee_proposal_window_size = 10`, a proposer whose oracle is compromised will emit `fee_proposal = fee_actual × 1.002` every block they propose. The `fee_actual` (median of the last 10 proposals) rises by up to 0.2% per block. Over 3,500 blocks (~1 hour at 1 block/second), the L2 gas price grows by `(1.002)^3500 ≈ 1,100×`. Over 10,000 blocks (~2.8 hours), it grows by `(1.002)^10000 ≈ 485,000×`. This causes all user transactions to pay wildly inflated fees or fail resource-bound checks, and the wrong `fee_proposal` is permanently committed into the `ProposalCommitment` hash that consensus signs over.

---

### Likelihood Explanation

**Likelihood: High.**

The `ExchangeRateOracleClient` is configured with a list of third-party HTTP endpoints (`url_header_list`). Any operator who configures even a single URL pointing to a compromised or attacker-controlled endpoint — or whose primary endpoint is compromised — triggers this path. The `index` is stored and the same URL is retried first on subsequent queries (line 142–143), so a compromised URL that responds first will be used persistently. No privilege is required beyond controlling one of the configured oracle URLs. [10](#0-9) 

---

### Recommendation

1. **Cross-validate across all URLs**: Query all configured URLs in parallel and compute the median (or trimmed mean) of the returned rates. Reject any rate that deviates from the median by more than a configurable threshold (e.g., 10%).
2. **Add a sanity bound on `strk_usd_rate`**: Reject rates outside a plausible range (e.g., `[10^15, 10^21]` for STRK/USD with 18 decimals) before passing them to `compute_fee_target`.
3. **Require a minimum number of successful responses**: Only accept a rate if at least `N` of the configured URLs agree within the threshold, analogous to the median-based `compute_fee_actual` already used for `fee_proposal` aggregation.
4. **Alert on divergence**: Log a warning and freeze `fee_proposal` at `fee_actual` (the same behavior as an oracle error) when the spread between URL responses exceeds the threshold.

---

### Proof of Concept

**Setup**: Configure `ExchangeRateOracleClient` with two URLs: `[attacker_url, honest_url]`. The attacker controls `attacker_url` and makes it return `{"price": "0x1", "decimals": 18}` (rate = 1, the minimum non-zero value).

**Step 1**: `spawn_query` starts at `index = 0` (attacker URL). The attacker URL responds first with `rate = 1`. [11](#0-10) 

**Step 2**: `fetch_rate` caches `rate = 1` for the current quantized timestamp. [12](#0-11) 

**Step 3**: `resolve_fee_target` calls `compute_fee_target(3_000_000_000, 1)`:
```
numerator = 3_000_000_000 × 10^18 = 3 × 10^27
floor = 3 × 10^27 / 1 = 3 × 10^27 > u128::MAX → saturates to u128::MAX
fee_target = GasPrice(u128::MAX)
``` [13](#0-12) 

**Step 4**: `compute_fee_proposal` clamps to `fee_actual × 1.002`. With `fee_actual = 10^10` FRI (10 GFri), `fee_proposal = 10,020,000,000`. The validator accepts this.

**Step 5**: After `window_size = 10` blocks all proposed at the upper bound, `fee_actual` rises to `10^10 × (1.002)^10 ≈ 1.0202 × 10^10`. The process repeats, compounding every block.

**Step 6**: The wrong `fee_proposal` is committed into `ProposalCommitment = Poseidon(partial_block_hash, 10_020_000_000)` and signed by consensus, permanently recording the manipulated fee on-chain. [8](#0-7)

### Citations

**File:** crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs (L114-145)
```rust
        let future = async move {
            let initial_index = index_clone.load(Ordering::SeqCst);
            for (i, url_and_headers) in
                url_header_list.iter().cycle().skip(initial_index).take(list_len).enumerate()
            {
                let UrlAndHeaderMap { mut url, headers } = url_and_headers.clone();
                url.query_pairs_mut().append_pair("timestamp", &adjusted_timestamp.to_string());
                let result = tokio::time::timeout(Duration::from_secs(query_timeout_sec), async {
                    let response = client
                        .get(url.clone())
                        .headers(headers.peek_secret().clone())
                        .send()
                        .await?;
                    if !response.status().is_success() {
                        return Err(ExchangeRateOracleClientError::RequestError(format!(
                            "Request failed with status {}: {}",
                            response.status(),
                            response.text().await?
                        )));
                    }
                    let body = response.text().await?;
                    let rate = resolve_query(body, &metrics)?;
                    Ok::<_, ExchangeRateOracleClientError>(rate)
                })
                .await;

                match result {
                    Ok(Ok(rate)) => {
                        let idx = (i + initial_index) % list_len;
                        index_clone.store(idx, Ordering::SeqCst);
                        debug!("Resolved query to {url} with rate {rate}");
                        return Ok(rate);
```

**File:** crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs (L185-212)
```rust
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
```

**File:** crates/apollo_l1_gas_price/src/exchange_rate_oracle.rs (L272-277)
```rust
        // Make sure to cache the result.
        cache.put(quantized_timestamp, rate);
        // We don't need to come back to this query since we have the result in cache.
        queries.pop(&quantized_timestamp);
        debug!("Caching conversion rate for timestamp {timestamp}, with rate {rate}");
        Ok(rate)
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L452-465)
```rust
        match self.deps.l1_gas_price_provider.get_strk_to_usd_rate(timestamp).await {
            Ok(rate) => {
                let target = compute_fee_target(target_atto_usd_per_l2_gas, rate);
                match target {
                    Some(t) => SNIP35_FEE_TARGET_FRI.set_lossy(t.0),
                    None => warn!("STRK/USD oracle returned zero rate, freezing fee_proposal"),
                }
                target
            }
            Err(e) => {
                warn!("STRK/USD oracle error: {e:?}, freezing fee_proposal");
                None
            }
        }
```

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L56-92)
```rust
pub fn compute_fee_actual(
    fee_proposals_window: &BTreeMap<BlockNumber, Option<GasPrice>>,
    height: BlockNumber,
    window_size: u64,
) -> Option<GasPrice> {
    let Some(start) = height.0.checked_sub(window_size) else {
        warn!(
            "Cannot compute fee_actual for height {height}: height is below window_size \
             ({window_size})"
        );
        return None;
    };
    let window_size_usize = usize::try_from(window_size).expect("window_size fits in usize");
    let mut window = Vec::with_capacity(window_size_usize);
    for source_height in (start..height.0).map(BlockNumber) {
        match fee_proposals_window.get(&source_height) {
            Some(Some(price)) => window.push(*price),
            Some(None) | None => {
                warn!(
                    "Cannot compute fee_actual for height {height}: fee_proposals_window has no \
                     recorded fee_proposal for height {source_height}"
                );
                return None;
            }
        }
    }
    window.sort();
    let mid = window_size_usize / 2;
    let median = if window_size_usize.is_multiple_of(2) {
        // Even: average of the two middle values, rounded down.
        // Overflow-safe averaging: a + (b - a) / 2 (safe because sorted, so b >= a).
        GasPrice(window[mid - 1].0 + (window[mid].0 - window[mid - 1].0) / 2)
    } else {
        window[mid]
    };
    Some(median)
}
```

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L102-113)
```rust
pub fn compute_fee_target(
    target_atto_usd_per_l2_gas: u128,
    strk_usd_rate: u128,
) -> Option<GasPrice> {
    if strk_usd_rate == 0 {
        return None;
    }
    // floor_fri = target_atto_usd_per_l2_gas * 10^18 / strk_usd_rate
    let numerator = U256::from(target_atto_usd_per_l2_gas) * U256::from(FRI_DECIMALS_SCALE);
    let floor = numerator / U256::from(strk_usd_rate);
    Some(GasPrice(u128::try_from(floor).unwrap_or(u128::MAX)))
}
```

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L118-128)
```rust
pub fn compute_fee_proposal(
    fee_target: Option<GasPrice>,
    fee_actual: GasPrice,
    margin_ppt: u128,
) -> GasPrice {
    let Some(fee_target) = fee_target else {
        return fee_actual;
    };
    let (lower, upper) = fee_proposal_bounds(fee_actual, margin_ppt);
    GasPrice(fee_target.0.clamp(lower, upper))
}
```

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L163-171)
```rust
pub(crate) fn proposal_commitment_from(
    partial: PartialBlockHash,
    fee_proposal: Option<GasPrice>,
) -> ProposalCommitment {
    let Some(fee_proposal) = fee_proposal else {
        return ProposalCommitment(partial.0);
    };
    ProposalCommitment(Poseidon::hash_array(&[partial.0, Felt::from(fee_proposal.0)]))
}
```

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_4.json (L1-9)
```json
{
    "fee_proposal_margin_ppt": 2,
    "fee_proposal_window_size": 10,
    "gas_price_max_change_denominator": 48,
    "gas_target": 1040000000,
    "max_block_size": 5800000000,
    "min_gas_price": "0x1dcd65000",
    "l1_gas_price_margin_percent": 10
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L396-416)
```rust
    // Validate fee_proposal is within the configured margin of fee_actual.
    // During initiation (fee_actual is None, <window_size blocks), bounds are not enforced.
    if let (Some(fee_actual), Some(fee_proposal)) =
        (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri)
    {
        let (lower_bound, upper_bound) = fee_proposal_bounds(
            fee_actual,
            VersionedConstants::latest_constants().fee_proposal_margin_ppt,
        );
        if fee_proposal.0 < lower_bound || fee_proposal.0 > upper_bound {
            return Err(ValidateProposalError::InvalidProposalInit(
                init_proposed.clone(),
                proposal_init_validation.clone(),
                format!(
                    "Fee proposal out of bounds: fee_actual={}, fee_proposal={}, allowed \
                     range=[{lower_bound}, {upper_bound}]",
                    fee_actual.0, fee_proposal.0
                ),
            ));
        }
    }
```

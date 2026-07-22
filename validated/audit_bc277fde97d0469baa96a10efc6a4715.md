### Title
Proposer-controlled `timestamp` in `ProposalInit` used without lower-bound check to fetch L1 gas prices, enabling stale-price commitment — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_proposal_init_valid` uses the proposer-supplied `init_proposed.timestamp` as the key to look up L1 gas prices on the validator side. The timestamp is bounded above (must not exceed `now + block_timestamp_window_seconds`) but has **no lower bound relative to `now`**. `L1GasPriceProvider::get_price_info` mirrors this gap: it rejects timestamps too far in the **future** but silently serves historical prices for any timestamp in the **past**. A malicious proposer can therefore anchor the block to an arbitrarily old timestamp (bounded only by `last_block_timestamp`), supply matching historical gas prices, and have every honest validator independently reproduce the same stale reference — causing the block to be committed with L1 gas prices that do not reflect the current market.

---

### Finding Description

**Asymmetric timestamp window in `is_proposal_init_valid`** [1](#0-0) 

The validator reads `now` from its own clock but only enforces a ceiling:

```
init_proposed.timestamp ≤ now + block_timestamp_window_seconds   // enforced
init_proposed.timestamp ≥ last_block_timestamp                   // enforced
init_proposed.timestamp ≥ now − <nothing>                        // MISSING
```

`block_timestamp_window_seconds` is configured to **1 second** in the production schema, so the future direction is tightly capped. [2](#0-1) 

The past direction has no symmetric cap. The only floor is `last_block_timestamp`, which can be arbitrarily old after a liveness gap.

**Proposer-controlled timestamp drives the validator's price lookup** [3](#0-2) 

The validator calls `get_l1_prices_in_fri_and_wei(init_proposed.timestamp, …)` — the proposer's timestamp, not `now` — to obtain the reference prices it will compare against the proposer's claimed prices.

**`get_price_info` has no lower-bound staleness guard** [4](#0-3) 

The only guard is `timestamp > last_timestamp + max_time_gap_seconds` (future direction). There is no symmetric check `timestamp < first_timestamp − margin` or `timestamp < now − max_time_gap_seconds`. With `max_time_gap_seconds = 900` (15 minutes) and `lag_margin_seconds = 60 s`, a past timestamp up to ~16 minutes old will silently return a valid historical mean. [5](#0-4) 

**Price comparison uses the stale reference** [6](#0-5) 

The `within_margin` check compares the proposer's prices against the stale reference fetched with the past timestamp. If the proposer also supplies prices that match that historical window, every honest validator independently fetches the same stale reference and the check passes.

---

### Impact Explanation

A block committed with stale L1 gas prices carries incorrect `l1_gas_price` and `l1_data_gas_price` fields in its header. These values are the authoritative inputs to fee calculation for every transaction in the block. Underpriced blocks allow users to pay less than the true L1 cost (economic loss to the protocol/sequencer); overpriced blocks overcharge users. Either direction constitutes an incorrect fee/gas/L1-gas-price effect with direct economic impact — matching the **Critical** impact category: *"Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact."*

---

### Likelihood Explanation

Any consensus participant selected as proposer can craft a `ProposalInit` with a past timestamp. The attack requires no special privilege beyond being a proposer in a given round, which rotates among validators. Historical L1 gas price data is publicly observable on-chain, so the attacker can pre-compute matching prices for any past timestamp within the 15-minute window. The attack is deterministic and leaves no on-chain evidence distinguishing it from a legitimate slow-block scenario.

---

### Recommendation

1. **Add a symmetric lower-bound check in `is_proposal_init_valid`** — mirror the existing upper-bound window:

```rust
if now > proposal_init_validation.block_timestamp_window_seconds
    && init_proposed.timestamp
        < now - proposal_init_validation.block_timestamp_window_seconds
{
    return Err(ValidateProposalError::InvalidProposalInit(
        init_proposed.clone(),
        proposal_init_validation.clone(),
        format!("Timestamp is too old relative to now: now={now}, proposed={}",
                init_proposed.timestamp),
    ));
}
```

2. **Fetch the validator's reference prices using `now`, not `init_proposed.timestamp`** — the validator should derive its own independent price reference from the current wall-clock time and then check that the proposer's prices fall within margin of that reference. The proposer's timestamp should only be used to set the block header field, not to drive the price lookup.

3. **Add a lower-bound staleness guard in `get_price_info`** — reject timestamps older than `now − max_time_gap_seconds` symmetrically with the existing upper-bound guard.

---

### Proof of Concept

1. Honest network produces block N at wall-clock time T. `last_block_timestamp = T`.
2. L1 gas price at time T−600 s was `P_old`; current price at T+Δ is `P_new` (assume `P_new > P_old` after a gas spike).
3. Malicious proposer is selected for block N+1 at time T+Δ.
4. Proposer crafts `ProposalInit` with:
   - `timestamp = T` (equal to `last_block_timestamp`, passes the `>= last_block_timestamp` check)
   - `l1_gas_price_fri / wei = P_old` (prices matching the historical mean at `T − lag_margin = T − 60 s`)
5. Proposer broadcasts this `ProposalInit`.
6. Each honest validator calls `is_proposal_init_valid`:
   - Timestamp check: `T >= T` ✓ and `T <= now + 1` ✓ (passes both bounds).
   - Price lookup: `get_l1_prices_in_fri_and_wei(T, …)` → `get_price_info(BlockTimestamp(T))` → no staleness error (T ≤ last_sample + 900 s) → returns historical mean anchored at `T − 60 s` = `P_old`.
   - `within_margin(P_old, P_old, margin)` → ✓.
7. Block N+1 is committed with `l1_gas_price = P_old` instead of `P_new`.
8. All transactions in block N+1 are charged fees based on the stale, lower L1 gas price. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L253-328)
```rust
async fn is_proposal_init_valid(
    proposal_init_validation: &ProposalInitValidation,
    init_proposed: &ProposalInit,
    clock: &dyn Clock,
    l1_gas_price_provider: Arc<dyn L1GasPriceProviderClient>,
    gas_price_params: &GasPriceParams,
) -> ValidateProposalResult<()> {
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
    if init_proposed.starknet_version != proposal_init_validation.starknet_version {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "starknet_version mismatch: expected={:?}, proposed={:?}",
                proposal_init_validation.starknet_version, init_proposed.starknet_version
            ),
        ));
    }
    // `version_constant_commitment` is proposer-supplied (network-derived). It is not yet a real
    // commitment (see `expected_version_constant_commitment`): the only valid value is the
    // sentinel, so reject anything else. Enforcing the same value the proposer emits keeps the two
    // sides in lockstep, so a real value cannot ship on one side without the other.
    let expected_commitment = expected_version_constant_commitment();
    if init_proposed.version_constant_commitment != expected_commitment {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "version_constant_commitment mismatch: expected={expected_commitment:?}, \
                 proposed={:?}",
                init_proposed.version_constant_commitment
            ),
        ));
    }
    if !(init_proposed.height == proposal_init_validation.height
        && init_proposed.l1_da_mode == proposal_init_validation.l1_da_mode
        && init_proposed.l2_gas_price_fri == proposal_init_validation.l2_gas_price_fri)
    {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            "ProposalInit validation failed".to_string(),
        ));
    }
    let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
        l1_gas_price_provider,
        init_proposed.timestamp,
        proposal_init_validation.previous_proposal_init.as_ref(),
        gas_price_params,
    )
    .await;
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

**File:** crates/apollo_node/resources/config_schema.json (L2787-2791)
```json
  "consensus_manager_config.context_config.static_config.block_timestamp_window_seconds": {
    "description": "Maximum allowed deviation (seconds) of a proposed block's timestamp from the current time.",
    "privacy": "Public",
    "value": 1
  },
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L123-155)
```rust
    pub fn get_price_info(&self, timestamp: BlockTimestamp) -> L1GasPriceProviderResult<PriceInfo> {
        let Some(samples) = &self.price_samples_by_block else {
            return Err(L1GasPriceProviderError::NotInitializedError);
        };
        // timestamp of the newest price sample
        let last_timestamp = samples
            .back()
            .ok_or(L1GasPriceProviderError::MissingDataError {
                timestamp: timestamp.0,
                lag: self.config.lag_margin_seconds.as_secs(),
            })?
            .timestamp;

        // Check if the prices are stale.
        if timestamp.0 > (*last_timestamp + self.config.max_time_gap_seconds) {
            return Err(L1GasPriceProviderError::StaleL1GasPricesError {
                current_timestamp: timestamp.0,
                last_valid_price_timestamp: *last_timestamp,
            });
        }

        // This index is for the last block in the mean (inclusive).
        let index_last_timestamp_rev = samples.iter().rev().position(|data| {
            data.timestamp <= timestamp.saturating_sub(&self.config.lag_margin_seconds.as_secs())
        });

        // Could not find a block with the requested timestamp and lag.
        let Some(last_index_rev) = index_last_timestamp_rev else {
            return Err(L1GasPriceProviderError::MissingDataError {
                timestamp: timestamp.0,
                lag: self.config.lag_margin_seconds.as_secs(),
            });
        };
```

**File:** crates/apollo_l1_gas_price_config/src/config.rs (L117-128)
```rust
impl Default for L1GasPriceProviderConfig {
    fn default() -> Self {
        const MEAN_NUMBER_OF_BLOCKS: u64 = 300;
        Self {
            number_of_blocks_for_mean: MEAN_NUMBER_OF_BLOCKS,
            lag_margin_seconds: Duration::from_secs(60),
            storage_limit: usize::try_from(10 * MEAN_NUMBER_OF_BLOCKS).unwrap(),
            max_time_gap_seconds: 900, // 15 minutes
            eth_to_strk_oracle_config: ExchangeRateOracleConfig::default(),
            strk_to_usd_oracle_config: ExchangeRateOracleConfig::default(),
        }
    }
```

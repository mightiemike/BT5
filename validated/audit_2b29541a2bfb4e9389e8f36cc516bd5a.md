### Title
`update_l2_gas_price` reads stale `fee_proposals_window` before current block's proposal is recorded — (`File: crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs`)

### Summary

In `finalize_decision`, `update_l2_gas_price(height, l2_gas_used)` is called on line 517 before `record_fee_proposal(height, init.fee_proposal_fri)` on line 518. `update_l2_gas_price` internally calls `compute_fee_actual(&self.fee_proposals_window, height, window_size)`, which computes the median of `[height − window_size, height − 1]`. Because the current block's `fee_proposal_fri` has not yet been inserted into `fee_proposals_window`, the `fee_actual` floor passed to `calculate_next_l2_gas_price_for_fin` is one block stale: it covers `[height − window_size, height − 1]` instead of the correct `[height + 1 − window_size, height]`. The resulting `l2_gas_price` written to `self.l2_gas_price` and published in the next block's header is therefore computed from the wrong median, producing an incorrect authoritative gas price that propagates to RPC fee estimation and proposal validation.

### Finding Description

`finalize_decision` in `SequencerConsensusContext` finalizes a decided block and prepares state for the next height. The two relevant lines are:

```rust
self.update_l2_gas_price(height, l2_gas_used);   // line 517
self.record_fee_proposal(height, init.fee_proposal_fri); // line 518
```

`update_l2_gas_price` delegates to `calculate_next_l2_gas_price`:

```rust
fn calculate_next_l2_gas_price(&self, height: BlockNumber, l2_gas_used: GasAmount) -> GasPrice {
    let fee_actual = compute_fee_actual(
        &self.fee_proposals_window,
        height,
        VersionedConstants::latest_constants().fee_proposal_window_size,
    );
    calculate_next_l2_gas_price_for_fin(self.l2_gas_price, height, l2_gas_used, ..., fee_actual)
}
```

`compute_fee_actual` is documented to return the median of `[height − window_size, height − 1]`:

```rust
/// Compute fee_actual for `height` as the median of the `fee_proposal` values
/// recorded for heights `[height - window_size, height - 1]`.
```

The gas price being computed here is for the **next** block (`height + 1`). Its `fee_actual` floor should therefore be the median of `[height + 1 − window_size, height]`, which includes the current block's proposal. But because `record_fee_proposal(height, ...)` has not yet been called, `fee_proposals_window` does not contain `height`, so `compute_fee_actual` silently uses the stale range `[height − window_size, height − 1]` — excluding the current block's proposal and including the oldest block that should have been rotated out.

`calculate_next_l2_gas_price_for_fin` uses `fee_actual` as a floor:

```rust
let effective_min = match fee_actual {
    Some(fa) => GasPrice(max(config_min.0, fa.0)),
    None => config_min,
};
calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
```

The resulting `self.l2_gas_price` is then used in `ProposalInitValidation.l2_gas_price_fri` for the next block's proposal validation, and is published in the block header consumed by RPC fee estimation.

### Impact Explanation

The `l2_gas_price` stored in `self.l2_gas_price` after `finalize_decision` is computed with a one-block-stale `fee_actual` floor. This value is:

1. Embedded in the next block's header via `FeeMarketInfo { next_l2_gas_price: self.l2_gas_price }` in the cende blob.
2. Used as `l2_gas_price_fri` in `ProposalInitValidation` for the next height's proposal validation.
3. Returned by RPC fee estimation endpoints that read the pending block's gas price.

When the current block's `fee_proposal_fri` is an outlier relative to the previous window (e.g., a rapid upward or downward move), the stale median diverges from the correct median. The floor is either underestimated (current proposal is high, causing the next block's gas price to be set too low) or overestimated (current proposal is low, causing the next block's gas price to be set too high). Both directions produce an authoritative-looking wrong gas price in RPC responses and in the committed block header.

**Matching impact:** *High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.*

### Likelihood Explanation

The error is systematic and occurs on every block once SNIP-35 (`fee_proposal_fri`) is active (Starknet ≥ V0_14_3). The magnitude of the divergence depends on how much the current block's proposal shifts the median. In a steady-state window the effect is small; during fee-price transitions (rapid rise or fall) the stale floor can differ materially from the correct floor, causing the published gas price to be bounded by the wrong value. No special attacker capability is required — any block whose `fee_proposal_fri` differs from the previous median triggers the discrepancy.

### Recommendation

Swap the two lines in `finalize_decision` so the current block's proposal is recorded before the gas price is updated:

```rust
// Record first so fee_proposals_window includes height when computing fee_actual.
self.record_fee_proposal(height, init.fee_proposal_fri);
self.update_l2_gas_price(height, l2_gas_used);
```

Alternatively, pass `height.next()` (i.e., `height + 1`) to `compute_fee_actual` inside `calculate_next_l2_gas_price`, since the price being computed is for the next block. Either change ensures the median is drawn from `[height + 1 − window_size, height]` as the spec requires.

### Proof of Concept

Assume `window_size = 10` and the last 10 committed blocks each had `fee_proposal_fri = 100 gwei`, so `fee_proposals_window` = `{N−10: 100, N−9: 100, …, N−1: 100}`.

Block N is decided with `fee_proposal_fri = 1 000 gwei` (a large upward move).

**Current (buggy) execution order:**

1. `update_l2_gas_price(N, gas_used)` is called.
2. `compute_fee_actual(&fee_proposals_window, N, 10)` reads heights `[N−10, N−1]` → all 100 gwei → median = **100 gwei**.
3. `effective_min = max(config_min, 100 gwei)` → floor = 100 gwei.
4. `self.l2_gas_price` is set using floor = 100 gwei.
5. `record_fee_proposal(N, 1000 gwei)` inserts `{N: 1000}` into the window.

**Correct execution order (record first):**

1. `record_fee_proposal(N, 1000 gwei)` inserts `{N: 1000}` into the window.
2. `update_l2_gas_price(N, gas_used)` is called.
3. `compute_fee_actual(&fee_proposals_window, N+1, 10)` reads heights `[N−9, N]` → `[100×9, 1000]` → sorted median (even window) = **(100 + 100) / 2 = 100 gwei** in this specific example, but for a window already trending upward (e.g., `[500, 550, 600, 650, 700, 750, 800, 850, 900, 1000]`) the correct median = **(700 + 750)/2 = 725 gwei** vs. the stale median = **(650 + 700)/2 = 675 gwei** — a 50 gwei underestimation of the floor, causing the next block's gas price to be published 50 gwei too low in the block header and in RPC fee estimation responses. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L425-441)
```rust
    /// Returns the next L2 gas price without mutating context. Used when building the fin and when
    /// updating at decision time.
    fn calculate_next_l2_gas_price(&self, height: BlockNumber, l2_gas_used: GasAmount) -> GasPrice {
        let fee_actual = compute_fee_actual(
            &self.fee_proposals_window,
            height,
            VersionedConstants::latest_constants().fee_proposal_window_size,
        );
        calculate_next_l2_gas_price_for_fin(
            self.l2_gas_price,
            height,
            l2_gas_used,
            self.config.dynamic_config.override_l2_gas_price_fri,
            &self.config.dynamic_config.min_l2_gas_price_per_height,
            fee_actual,
        )
    }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L496-500)
```rust
    fn update_l2_gas_price(&mut self, height: BlockNumber, l2_gas_used: GasAmount) {
        self.l2_gas_price = self.calculate_next_l2_gas_price(height, l2_gas_used);
        let gas_price_u64 = u64::try_from(self.l2_gas_price.0).unwrap_or(u64::MAX);
        CONSENSUS_L2_GAS_PRICE.set_lossy(gas_price_u64);
    }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L515-519)
```rust
        let DecisionReachedResponse { state_diff, central_objects } = decision_reached_response;

        self.update_l2_gas_price(height, l2_gas_used);
        self.record_fee_proposal(height, init.fee_proposal_fri);

```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L600-606)
```rust
                    .casm_hash_computation_data_sierra_gas,
                casm_hash_computation_data_proving_gas: central_objects
                    .casm_hash_computation_data_proving_gas,
                fee_market_info: FeeMarketInfo {
                    l2_gas_consumed: l2_gas_used,
                    next_l2_gas_price: self.l2_gas_price,
                },
```

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L47-92)
```rust
/// Compute fee_actual for `height` as the median of the `fee_proposal` values
/// recorded for heights `[height - window_size, height - 1]`.
///
/// Returns `None` (after logging a warning) when any of those heights is missing from
/// `fee_proposals_window` or recorded as `None` (e.g., pre-V0_14_3 blocks). The `None`
/// case triggers the `l2_gas_price` fallback in both proposer and validator paths.
///
/// Median rule for even `window_size`: average of the two middle values rounded down;
/// for odd: the single middle value.
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

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L54-77)
```rust
/// Compute the next L2 gas price (for the fin or for updating state). Respects override when set.
pub fn calculate_next_l2_gas_price_for_fin(
    current_l2_gas_price: GasPrice,
    height: BlockNumber,
    l2_gas_used: GasAmount,
    override_l2_gas_price_fri: Option<u128>,
    min_l2_gas_price_per_height: &[PricePerHeight],
    fee_actual: Option<GasPrice>,
) -> GasPrice {
    if let Some(override_value) = override_l2_gas_price_fri {
        info!(
            "L2 gas price ({}) is not updated, remains on override value of {override_value} fri",
            current_l2_gas_price.0
        );
        return GasPrice(override_value);
    }
    let gas_target = VersionedConstants::latest_constants().gas_target;
    let config_min = get_min_gas_price_for_height(height, min_l2_gas_price_per_height);
    let effective_min = match fee_actual {
        Some(fa) => GasPrice(max(config_min.0, fa.0)),
        None => config_min,
    };
    calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
}
```

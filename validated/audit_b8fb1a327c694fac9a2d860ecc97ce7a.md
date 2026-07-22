### Title
Unconstrained `fee_proposal_fri` During V0_14_3 Version-Transition Window Allows Malicious Proposer to Seed Extreme L2 Gas Prices — (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

The `fee_proposal_fri` field in `ProposalInit` is validated against `fee_actual` (the median of the sliding window) only when `fee_actual` is `Some`. During the V0_14_3 version-transition window — the first `fee_proposal_window_size` (10) blocks after activation — `fee_actual` is `None` because `compute_fee_actual` returns `None` whenever any window entry is `None` (pre-V0_14_3 blocks). The bounds check in `is_proposal_init_valid` is silently skipped under this condition, allowing a malicious proposer to publish an arbitrary `fee_proposal_fri`. Those extreme values are recorded in `fee_proposals_window`, and once the window fills with `Some` entries, `fee_actual` becomes the median of the attacker-seeded values, driving the L2 gas price to an attacker-chosen level for all subsequent blocks.

---

### Finding Description

**Version/config boundary root cause.** The `VersionedConstants` struct defines `fee_proposal_margin_ppt = 2` (0.2 ppt) and `fee_proposal_window_size = 10` as the invariants that bound how far a proposer's `fee_proposal_fri` may deviate from the network-agreed `fee_actual`. [1](#0-0) 

The enforcement gate in `is_proposal_init_valid` is:

```rust
if let (Some(fee_actual), Some(fee_proposal)) =
    (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri)
{
    // bounds check
}
``` [2](#0-1) 

`fee_actual` is computed by `compute_fee_actual`, which returns `None` if **any** entry in the window is `None`:

```rust
Some(None) | None => {
    warn!("...");
    return None;
}
``` [3](#0-2) 

`initialize_fee_proposals_window` populates the window from state-sync storage, recording `None` for every pre-V0_14_3 block: [4](#0-3) 

Therefore, for the entire 10-block window after V0_14_3 activation, `fee_actual` is `None` and the bounds check is bypassed. A malicious proposer can publish `fee_proposal_fri = u128::MAX` (or any extreme value) in each of those 10 `ProposalInit` messages. Honest validators also compute `fee_actual = None` and accept the proposal without objection. [5](#0-4) 

After the window fills, `compute_fee_actual` returns the median of the attacker-seeded values. `calculate_next_l2_gas_price` then uses this corrupted `fee_actual` to set the L2 gas price for all subsequent blocks: [6](#0-5) 

The corrupted `fee_proposal_fri` is also committed into the `ProposalCommitment` via `Poseidon(partial.0, fee_proposal_fri)`, so the wrong value is permanently bound to the block hash: [7](#0-6) 

---

### Impact Explanation

A malicious proposer who controls the block-proposal role during the 10-block V0_14_3 transition window can seed `fee_proposals_window` with extreme `fee_proposal_fri` values. Once the window is full, `fee_actual` equals the median of those values, and `calculate_next_l2_gas_price` drives the L2 gas price to an attacker-chosen level. This constitutes an **incorrect fee/gas accounting effect with direct economic impact**: users are overcharged or undercharged for L2 gas, and the wrong price is committed into block headers and the `ProposalCommitment` hash.

Matches: *Critical — Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact.*

---

### Likelihood Explanation

The attack window is predictable and bounded: exactly `fee_proposal_window_size` (10) blocks after V0_14_3 activation. In Starknet's current single-sequencer deployment the proposer role is held by one operator, making the window trivially exploitable by that operator. In a future decentralized deployment, an attacker must control the proposer slot for a majority of those 10 blocks to fully control the median; controlling even 6 of 10 slots suffices to set the median to an extreme value.

---

### Recommendation

1. **Enforce a hard cap on `fee_proposal_fri` regardless of `fee_actual` availability.** Even when `fee_actual` is `None`, reject any `fee_proposal_fri` that exceeds a configurable absolute maximum (e.g., a multiple of `min_gas_price` from `VersionedConstants`).

2. **Seed the window with a safe default at V0_14_3 activation.** When `initialize_fee_proposals_window` encounters pre-V0_14_3 `None` entries, substitute the configured `min_gas_price` rather than propagating `None`, so `fee_actual` is always `Some` from the first V0_14_3 block onward.

3. **Alternatively, treat a partially-`None` window as a partial median** over only the `Some` entries, rather than returning `None` for the entire window.

---

### Proof of Concept

1. Network upgrades to V0_14_3 at block height `H`.
2. `initialize_fee_proposals_window(H)` reads blocks `[H-10, H)` from state-sync; all return `fee_proposal_fri = None` (pre-V0_14_3). The window is `{H-10: None, ..., H-1: None}`.
3. For blocks `H` through `H+9`, the malicious proposer publishes `ProposalInit { fee_proposal_fri: Some(GasPrice(u128::MAX)), ... }`.
4. Each validator calls `compute_fee_actual(&window, height, 10)`. Since the window still contains `None` entries, it returns `None`. The bounds check at line 398 of `validate_proposal.rs` is skipped. The proposal is accepted.
5. `record_fee_proposal(height, Some(GasPrice(u128::MAX)))` is called for each of the 10 blocks.
6. At block `H+10`, `compute_fee_actual` finds all 10 entries are `Some(u128::MAX)` and returns `Some(GasPrice(u128::MAX))`.
7. `calculate_next_l2_gas_price(H+10, ...)` uses `fee_actual = u128::MAX`, driving the L2 gas price to `u128::MAX` (capped by `max_block_size` arithmetic).
8. All subsequent transactions pay fees computed against this extreme L2 gas price. [8](#0-7) [2](#0-1) [9](#0-8)

### Citations

**File:** crates/apollo_versioned_constants/src/lib.rs (L27-30)
```rust
    /// Number of `fee_proposal` values used to compute `fee_actual` (sliding window).
    pub fee_proposal_window_size: u64,
    /// Maximum `fee_proposal` change per block in parts per thousand (e.g., `2` = 0.2%).
    pub fee_proposal_margin_ppt: u128,
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

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L56-91)
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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L326-353)
```rust
    pub async fn initialize_fee_proposals_window(
        &mut self,
        start_height: BlockNumber,
    ) -> StateSyncClientResult<()> {
        const STATE_SYNC_RETRY_INTERVAL: Duration = Duration::from_millis(500);
        let window_size = VersionedConstants::latest_constants().fee_proposal_window_size;
        let window_end_height = start_height.0;
        let window_start_height = window_end_height.saturating_sub(window_size);
        let mut pending_heights: VecDeque<BlockNumber> =
            (window_start_height..window_end_height).map(BlockNumber).collect();
        while let Some(block_number) = pending_heights.pop_front() {
            match self.deps.state_sync_client.get_block(block_number).await {
                Ok(block) => self.record_fee_proposal(
                    block_number,
                    block.block_header_without_hash.fee_proposal_fri,
                ),
                Err(StateSyncClientError::StateSyncError(StateSyncError::BlockNotFound(_))) => {
                    warn!(
                        "State sync not ready for height {block_number}; re-queueing after \
                         {STATE_SYNC_RETRY_INTERVAL:?}"
                    );
                    pending_heights.push_back(block_number);
                    tokio::time::sleep(STATE_SYNC_RETRY_INTERVAL).await;
                }
                Err(e) => return Err(e),
            }
        }
        Ok(())
```

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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L895-900)
```rust
                    fee_actual: compute_fee_actual(
                        &self.fee_proposals_window,
                        init.height,
                        VersionedConstants::latest_constants().fee_proposal_window_size,
                    ),
                };
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

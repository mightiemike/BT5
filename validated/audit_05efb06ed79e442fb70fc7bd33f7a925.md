### Title
Unbounded `fee_proposal_fri` During SNIP-35 Initiation Window Permanently Corrupts L2 Gas Price Floor — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

During the SNIP-35 initiation period — the first `window_size` (10) blocks after the V0_14_3 upgrade — `compute_fee_actual` returns `None` because the sliding window still contains pre-upgrade blocks recorded as `None`. The `is_proposal_init_valid` function explicitly skips the `fee_proposal_fri` bounds check when `fee_actual` is `None`. A malicious proposer can therefore set `fee_proposal_fri = u128::MAX` in `ProposalInit` with no rejection. These values are stored in every node's `fee_proposals_window` and in `BlockHeaderWithoutHash`. Once the window fills, `fee_actual = u128::MAX`, which becomes the `effective_min` floor in `calculate_next_l2_gas_price_for_fin`, driving the L2 gas price monotonically toward `u128::MAX`. Because the bounds check then enforces future `fee_proposal_fri` values within a small margin of this extreme
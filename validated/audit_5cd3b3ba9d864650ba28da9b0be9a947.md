### Title
Unchecked `fee_proposal_fri` in `ProposalInit` During Startup Window Allows Arbitrary Block Commitment Hash and Fee-Oracle Poisoning — (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

During the startup phase (first `fee_proposal_window_size` blocks), the `fee_proposal_fri` field in `ProposalInit` is accepted without any bounds validation. A malicious proposer can supply any value (e.g., `u128::MAX`). That value is (1) fed directly into `proposal_commitment_from` to produce the canonical block commitment hash, and (2) stored in the sliding window that drives future `fee_actual` calculations, permanently widening the margin within which subsequent proposers may set extreme gas prices.

---

### Finding Description

`is_proposal_init_valid` in `validate_proposal.rs` guards `fee_proposal_fri` only when `fee_actual` is `Some`:

```rust
// validate_proposal.rs lines 396-416
if let (Some(fee_actual), Some(fee_proposal)) =
    (proposal_init_validation.fee_actual, init_proposed.fee_proposal_fri)
{
    let (lower_bound, upper_bound) = fee_proposal_bounds(
        fee_actual,
        VersionedConstants::latest_constants().fee_proposal_margin_ppt,
    );
    if fee_proposal.0 < lower_bound || fee_proposal.0 > upper_bound {
        return Err(ValidateProposalError::InvalidProposalInit(...));
    }
}
```

The comment above this block explicitly states: *"During initiation (fee_actual is None, <window_size blocks), bounds are not enforced."* [1](#0-0) 

`fee_actual` is `None` until the sliding window accumulates `fee_proposal_window_size` entries. During that entire startup window every proposer-supplied `fee_proposal_fri` value passes validation unconditionally.

The unchecked value is then used in two ways:

**1. Block commitment hash.** In `handle_proposal_part`, the proposer's `fee_proposal_fri` is passed directly to `proposal_commitment_from`:

```rust
// validate_proposal.rs lines 582-585
let batcher_block_commitment = proposal_commitment_from(
    finished_info.proposal_commitment.partial_block_hash,
    fee_proposal,   // ← proposer-supplied, unchecked during startup
);
``` [2](#0-1) 

This means the canonical block commitment hash committed to by consensus (and ultimately anchored on L1) encodes an arbitrary proposer-chosen value.

**2. Sliding-window poisoning.** The committed `fee_proposal_fri` values from startup blocks are accumulated in the sliding window. Once the window fills, `fee_actual` is derived from those values. All subsequent `fee_proposal_fri` bounds are computed relative to this poisoned `fee_actual`:

```rust
// validate_proposal.rs lines 401-404
let (lower_bound, upper_bound) = fee_proposal_bounds(
    fee_actual,
    VersionedConstants::latest_constants().fee_proposal_margin_ppt,
);
``` [3](#0-2) 

If `fee_actual` is anchored to an extreme startup value, the margin band for every future block is proportionally extreme, allowing a colluding proposer to set `fee_proposal_fri` far outside any economically reasonable range.

The `fee_proposal_fri` field is required for `StarknetVersion >= V0_14_3` and is absent from the `l1_gas_price_margin_percent` check that guards the four L1 price fields, so there is no secondary gate: [4](#0-3) 

---

### Impact Explanation

- **Wrong block commitment hash (Critical – Wrong state).** The block commitment is the canonical identifier used by consensus and L1 anchoring. Encoding an arbitrary `fee_proposal_fri` produces a commitment that diverges from what an honest node would compute, constituting a wrong committed state value.

- **Fee-oracle poisoning (Critical – Incorrect fee with economic impact).** Extreme startup values permanently skew `fee_actual`, widening the margin band for all subsequent blocks. A colluding proposer can exploit this to propose `fee_proposal_fri` values orders of magnitude above or below the true market rate, distorting the SNIP-35 fee signal that users and wallets rely on for gas estimation.

---

### Likelihood Explanation

In BFT consensus any validator may become a proposer. The startup window spans the first `fee_proposal_window_size` blocks of a new chain or after a restart with an empty window. A single malicious proposer winning one slot during that window is sufficient to poison the entire window.

---

### Recommendation

Apply a fallback bound when `fee_actual` is `None`. For example, use the configured minimum L2 gas price as a synthetic `fee_actual` during startup, or enforce an absolute cap (e.g., `max_l2_gas_price` from `VersionedConstants`) on `fee_proposal_fri` regardless of window state. This mirrors the resolution applied to the swETH `reprice()` issue: add an upper/lower bound check that does not depend on a previously-established reference value being available.

---

### Proof of Concept

1. A validator wins a proposer slot during the first `fee_proposal_window_size` blocks.
2. It constructs a `ProposalInit` with `fee_proposal_fri = Some(GasPrice(u128::MAX))`.
3. `is_proposal_init_valid` is called; `proposal_init_validation.fee_actual` is `None`; the `if let` guard does not fire; the value passes.
4. `proposal_commitment_from(partial_block_hash, Some(GasPrice(u128::MAX)))` is called, producing a block commitment that encodes `u128::MAX`.
5. All honest validators compute the same commitment (they all use the proposer's init value) and the block is finalized with this commitment.
6. The value `u128::MAX` enters the sliding window. After `fee_proposal_window_size` such blocks, `fee_actual ≈ u128::MAX`.
7. For all subsequent blocks, `fee_proposal_bounds(fee_actual, margin_ppt)` returns `[lower, upper]` anchored to `u128::MAX`, allowing any proposer to set `fee_proposal_fri` to an extreme value within that band without triggering `InvalidProposalInit`.

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L370-394)
```rust
    // fee_proposal is required iff Starknet version >= V0_14_3.
    let fee_proposal_required = init_proposed.starknet_version >= StarknetVersion::V0_14_3;
    match (init_proposed.fee_proposal_fri, fee_proposal_required) {
        (Some(_), false) => {
            return Err(ValidateProposalError::InvalidProposalInit(
                init_proposed.clone(),
                proposal_init_validation.clone(),
                format!(
                    "fee_proposal must be absent before V0_14_3, got Some at version {}",
                    init_proposed.starknet_version
                ),
            ));
        }
        (None, true) => {
            return Err(ValidateProposalError::InvalidProposalInit(
                init_proposed.clone(),
                proposal_init_validation.clone(),
                format!(
                    "fee_proposal is required at V0_14_3+, got None at version {}",
                    init_proposed.starknet_version
                ),
            ));
        }
        _ => {}
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L582-585)
```rust
            let batcher_block_commitment = proposal_commitment_from(
                finished_info.proposal_commitment.partial_block_hash,
                fee_proposal,
            );
```

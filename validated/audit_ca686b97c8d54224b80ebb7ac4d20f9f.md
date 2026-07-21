### Title
`l1_gas_price_margin_percent` (ETH-to-FRI rate margin) reused for WEI gas price deviation check in `is_proposal_init_valid` — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

`VersionedConstants::l1_gas_price_margin_percent` is documented as "The margin for the eth to fri rate disagreement" and is sized specifically to tolerate the fact that different sequencer nodes may use slightly different ETH-to-STRK exchange rates when converting WEI prices to FRI. However, the same setting is also applied verbatim to validate the raw WEI gas prices (`l1_gas_price_wei`, `l1_data_gas_price_wei`) in `is_proposal_init_valid`. WEI prices involve no ETH-to-STRK conversion at all — they are raw Ethereum gas prices read directly from L1 — so the tolerance needed for WEI disagreement is fundamentally different (and should be much smaller) than the tolerance needed for FRI disagreement. A malicious proposer can exploit the over-wide WEI margin to commit inflated or deflated L1 WEI gas prices into accepted blocks, causing incorrect fee accounting for all transactions in those blocks.

### Finding Description

In `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`, the function `is_proposal_init_valid` reads a single margin constant and applies it to four independent price checks:

```rust
let l1_gas_price_margin_percent =
    VersionedConstants::latest_constants().l1_gas_price_margin_percent.into();
// ...
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, l1_gas_price_margin_percent)
    && within_margin(l1_data_gas_price_fri_proposed, l1_data_gas_price_fri, l1_gas_price_margin_percent)
    && within_margin(l1_gas_price_wei_proposed, l1_gas_price_wei, l1_gas_price_margin_percent)
    && within_margin(l1_data_gas_price_wei_proposed, l1_data_gas_price_wei, l1_gas_price_margin_percent))
```

The `VersionedConstants` struct documents the field as:

> "The margin for the eth to fri rate disagreement, expressed as a percentage (parts per hundred)."

Its value is hardcoded at **10%** across all deployed versioned-constants JSON files (`orchestrator_versioned_constants_0_14_0.json` through `0_14_4.json`).

The FRI checks (checks 1 and 2) are the intended use: two nodes may compute different FRI prices from the same WEI price because they use slightly different ETH-to-STRK oracle rates, so a 10% band is reasonable. The WEI checks (checks 3 and 4) are the repurposed use: WEI prices are raw Ethereum gas prices read directly from L1 RPC endpoints. The only source of disagreement between nodes for WEI prices is minor timing differences in L1 block sampling, which should be far smaller than 10%. The 10% margin was never calibrated for WEI-price disagreement.

The `within_margin` function implements a symmetric band anchored to the validator's reference:

```rust
fn within_margin(proposed: GasPrice, reference: GasPrice, margin_percent: u128) -> bool {
    if proposed.0.abs_diff(reference.0) <= GAS_PRICE_ABS_DIFF_MARGIN { return true; }
    let margin = reference.0.saturating_mul(margin_percent) / 100;
    proposed.0.abs_diff(reference.0) <= margin
}
```

A proposer-supplied WEI price that is up to 10% above or below the validator's locally-observed WEI price will pass this check and be committed into the accepted block.

### Impact Explanation

The accepted `l1_gas_price_wei` and `l1_data_gas_price_wei` values are written into the block header and used downstream for L1 fee accounting. A proposer that consistently submits WEI prices at the upper edge of the 10% band inflates the L1 gas cost charged to every transaction in every block it proposes, extracting value from users. Conversely, a proposer that submits WEI prices at the lower edge deflates L1 fees, subsidizing transactions at the expense of the protocol. Because the validator accepts any value within the 10% band, this is not detectable as a protocol violation.

Additionally, if operators need to tighten `l1_gas_price_margin_percent` to reduce WEI-price manipulation, they will inadvertently tighten the FRI-price tolerance as well, potentially causing legitimate proposals to be rejected when the ETH-to-STRK oracle rates of proposer and validator diverge by more than the new, tighter value.

This matches the impact category: **Critical — Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact.**

### Likelihood Explanation

The proposer role rotates among sequencer nodes in the BFT protocol. Any node that acts as proposer can freely choose WEI prices anywhere within the 10% band without being rejected. No special privilege beyond being a scheduled proposer is required. The attack is repeatable every block the malicious node proposes.

### Recommendation

Define a separate versioned-constants field for the WEI gas price tolerance, e.g. `l1_gas_price_wei_margin_percent`, calibrated to the expected timing-based disagreement between nodes reading L1 RPC data (typically well under 1%). Apply `l1_gas_price_margin_percent` only to the FRI price checks (where ETH-to-STRK rate disagreement is the dominant source of variance) and apply the new `l1_gas_price_wei_margin_percent` to the WEI price checks. This mirrors the recommendation in the referenced external report: two checks that serve different purposes should not share the same tolerance parameter.

### Proof of Concept

1. `VersionedConstants::l1_gas_price_margin_percent` is defined and documented in `crates/apollo_versioned_constants/src/lib.rs` lines 24–26 as the ETH-to-FRI rate margin. [1](#0-0) 

2. All deployed versioned-constants JSON files set this value to `10` (10%). [2](#0-1) 

3. In `is_proposal_init_valid`, the same constant is loaded once and applied to all four price checks — both FRI and WEI — without any separate WEI-specific tolerance. [3](#0-2) 

4. The `within_margin` helper accepts any proposed value within `margin_percent`% of the validator's reference, meaning a proposer can submit WEI prices up to 10% above the validator's locally-observed L1 gas price and have the proposal accepted. [4](#0-3) 

5. The accepted WEI prices are committed into the block header and used for L1 fee accounting, creating a direct economic impact for every transaction in the block.

### Citations

**File:** crates/apollo_versioned_constants/src/lib.rs (L24-26)
```rust
    /// The margin for the eth to fri rate disagreement, expressed as a percentage (parts per
    /// hundred).
    pub l1_gas_price_margin_percent: u32,
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L329-368)
```rust
    let l1_gas_price_margin_percent =
        VersionedConstants::latest_constants().l1_gas_price_margin_percent.into();
    debug!("L1 price info: fri={l1_gas_prices_fri:?}, wei={l1_gas_prices_wei:?}");

    let l1_gas_price_fri = l1_gas_prices_fri.l1_gas_price;
    let l1_data_gas_price_fri = l1_gas_prices_fri.l1_data_gas_price;
    let l1_gas_price_wei = l1_gas_prices_wei.l1_gas_price;
    let l1_data_gas_price_wei = l1_gas_prices_wei.l1_data_gas_price;
    let l1_gas_price_fri_proposed = init_proposed.l1_gas_price_fri;
    let l1_data_gas_price_fri_proposed = init_proposed.l1_data_gas_price_fri;
    let l1_gas_price_wei_proposed = init_proposed.l1_gas_price_wei;
    let l1_data_gas_price_wei_proposed = init_proposed.l1_data_gas_price_wei;

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

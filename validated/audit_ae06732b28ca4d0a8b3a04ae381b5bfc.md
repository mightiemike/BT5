### Title
Unenforced Dual-Location Block-Gas-Capacity Invariant Causes Incorrect EIP-1559 Fee Market Calculation - (`crates/blockifier/src/bouncer.rs` / `crates/apollo_versioned_constants/src/lib.rs`)

### Summary

`BouncerWeights::receipt_l2_gas` (batcher config) and `apollo_versioned_constants::VersionedConstants::max_block_size` (orchestrator versioned constants) represent the same semantic value — the maximum L2 gas per block — but are stored in two independent, separately-configured locations. The code explicitly documents that they must stay in sync, but no enforcement mechanism exists. When they diverge, the EIP-1559 fee market computes the next gas price against a different block capacity than the one the batcher actually enforces, producing a persistently wrong `next_l2_gas_price` committed to every block header.

### Finding Description

**Location 1 — Batcher/Blockifier block-closing limit:**

`BouncerWeights::receipt_l2_gas` is the per-block ceiling used by the `Bouncer` to stop adding transactions. It is a runtime config field under `batcher_config.static_config.block_builder_config.bouncer_config.block_max_capacity.receipt_l2_gas`. [1](#0-0) 

Its default value is `GasAmount(5800000000)`, with the comment: [2](#0-1) 

**Location 2 — Orchestrator fee-market block-size:**

`VersionedConstants::max_block_size` is the per-Starknet-version constant used by `calculate_next_base_gas_price` to compute the EIP-1559 price adjustment. It is baked into per-version JSON files and loaded at startup. [3](#0-2) 

The comment on this field reads:

```
// NOTE: Must stay in sync with BouncerWeights receipt_l2_gas.
```

The versioned JSON files show the value has changed across versions:

| Version | `max_block_size` |
|---|---|
| V0_14_0 | 4 000 000 000 |
| V0_14_1 | 5 000 000 000 |
| V0_14_2+ | 5 800 000 000 | [4](#0-3) [5](#0-4) 

**The fee-market consumer:**

`calculate_next_base_gas_price` reads `max_block_size` from `VersionedConstants::latest_constants()` (the orchestrator VC) and uses it as the denominator for the congestion ratio: [6](#0-5) 

The `gas_delta` and `price_change` are computed relative to `gas_target`, which itself must be `< max_block_size`. The actual `gas_used` fed into this function is the block's `receipt_l2_gas` accumulation — the value bounded by the bouncer.

**No enforcement of equality:**

There is no test, startup assertion, or config-validation step that checks `BouncerWeights::default().receipt_l2_gas == VersionedConstants::latest_constants().max_block_size`. The only guard is a code comment in each file. [7](#0-6) 

### Impact Explanation

When `receipt_l2_gas` (bouncer) ≠ `max_block_size` (orchestrator VC):

- The batcher closes blocks at capacity `C_bouncer`.
- The fee market computes the congestion ratio as `gas_used / gas_target` where `gas_target < C_vc` (the orchestrator value).
- If `C_bouncer > C_vc`: blocks can be filled beyond what the fee market considers "full". The fee market sees `gas_used > max_block_size`, which violates the assertion `gas_target < versioned_constants.max_block_size` and panics, or if `gas_target` is set relative to `C_vc`, the price adjustment is computed against the wrong denominator, systematically underpricing gas during congestion.
- If `C_bouncer < C_vc`: blocks close before the fee market's ceiling, making every block appear under-congested, suppressing gas prices below the economically correct level.

Either direction produces a persistently wrong `next_l2_gas_price` written into every block header, constituting incorrect fee/gas accounting with direct economic impact on all users.

### Likelihood Explanation

The divergence is triggered by any of the following non-exceptional events:

1. **Starknet version upgrade**: A new `orchestrator_versioned_constants_X.json` is deployed with a changed `max_block_size` (as happened between V0_14_0 → V0_14_1 → V0_14_2). The batcher config `receipt_l2_gas` is not automatically updated.
2. **Operator capacity tuning**: An operator increases `receipt_l2_gas` in the batcher config to expand block capacity without updating the orchestrator VC JSON (which is compiled into the binary and cannot be changed at runtime without a new deployment).

Both scenarios are realistic operational events. The absence of any validation or test that cross-checks the two values means the divergence can persist silently across many blocks.

### Recommendation

Add a startup assertion (or config-validation step) that compares `BouncerConfig::block_max_capacity.receipt_l2_gas` against `apollo_versioned_constants::VersionedConstants::latest_constants().max_block_size` and fails fast if they differ. For example, in the node initialization path:

```rust
assert_eq!(
    bouncer_config.block_max_capacity.receipt_l2_gas,
    apollo_versioned_constants::VersionedConstants::latest_constants().max_block_size,
    "receipt_l2_gas must equal max_block_size; update batcher config or orchestrator VC"
);
```

Alternatively, eliminate the duplication entirely by having the fee market read `receipt_l2_gas` directly from the `BouncerConfig` rather than from a separate versioned-constants file, making the two components share a single source of truth.

### Proof of Concept

1. Deploy the sequencer with `orchestrator_versioned_constants_0_14_1.json` as the active version (`max_block_size = 5_000_000_000`) but leave the batcher config at its default `receipt_l2_gas = 5_800_000_000`.

2. Submit enough transactions to fill a block to `5_800_000_000` receipt-gas (the bouncer limit). The batcher closes the block at this level.

3. `calculate_next_base_gas_price` is called with `gas_used = 5_800_000_000` and `gas_target` derived from `max_block_size = 5_000_000_000` (e.g., `gas_target = 3_000_000_000`).

4. The fee market computes `gas_delta = |5_800_000_000 − 3_000_000_000| = 2_800_000_000` against a denominator of `3_000_000_000 × 48 = 144_000_000_000`, yielding a price increase of `price × 2_800_000_000 / 144_000_000_000 ≈ 1.94%` per block.

5. Had the correct `max_block_size = 5_800_000_000` been used, `gas_target` would be `3_480_000_000`, and the same `gas_used` would produce `gas_delta = 2_320_000_000` against `3_480_000_000 × 48 = 167_040_000_000`, yielding `≈ 1.39%` per block — a systematically different (higher) price trajectory.

6. The divergence compounds across every block, producing a `next_l2_gas_price` that is persistently wrong relative to actual network congestion. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** crates/blockifier/src/bouncer.rs (L155-168)
```rust
pub struct BouncerWeights {
    pub l1_gas: usize,
    pub message_segment_length: usize,
    pub n_events: usize,
    pub state_diff_size: usize,
    pub sierra_gas: GasAmount,
    pub n_txs: usize,
    pub proving_gas: GasAmount,
    /// Receipt-based L2 gas, including execution gas + state allocation costs + DA costs.
    /// Used to close blocks on the economic gas metric. Diverges from sierra_gas because
    /// it includes allocation_cost for new storage keys and other non-execution costs.
    // NOTE: Must stay in sync with orchestrator_versioned_constants' max_block_size.
    pub receipt_l2_gas: GasAmount,
}
```

**File:** crates/blockifier/src/bouncer.rs (L227-229)
```rust
            // NOTE: Must stay in sync with orchestrator_versioned_constants' max_block_size.
            receipt_l2_gas: GasAmount(5800000000),
        }
```

**File:** crates/apollo_versioned_constants/src/lib.rs (L8-31)
```rust
/// Versioned constants for the Consensus.
#[derive(Clone, Debug, Deserialize)]
pub struct VersionedConstants {
    ///  This is used to calculate the base gas price for the next block according to EIP-1559 and
    /// serves as a sensitivity parameter that limits the maximum rate of change of the gas price
    /// between consecutive blocks.
    pub gas_price_max_change_denominator: u128,
    /// The minimum gas price in fri.
    pub min_gas_price: GasPrice,
    /// The maximum block size in gas units.
    // NOTE: Must stay in sync with BouncerWeights receipt_l2_gas.
    // NOTE: When max_block_size is changed, update `gas_target` accordingly to maintain the ratio.
    pub max_block_size: GasAmount,
    /// The target gas usage per block. Used by EIP-1559 to calculate the next block's gas price.
    // Target is 60% of max_block_size, making price adjustment more responsive to congestion.
    pub gas_target: GasAmount,
    /// The margin for the eth to fri rate disagreement, expressed as a percentage (parts per
    /// hundred).
    pub l1_gas_price_margin_percent: u32,
    /// Number of `fee_proposal` values used to compute `fee_actual` (sliding window).
    pub fee_proposal_window_size: u64,
    /// Maximum `fee_proposal` change per block in parts per thousand (e.g., `2` = 0.2%).
    pub fee_proposal_margin_ppt: u128,
}
```

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_1.json (L1-9)
```json
{
    "fee_proposal_margin_ppt": 2,
    "fee_proposal_window_size": 10,
    "gas_price_max_change_denominator": 48,
    "gas_target": 4000000000,
    "max_block_size": 5000000000,
    "min_gas_price": "0x1dcd65000",
    "l1_gas_price_margin_percent": 10
}
```

**File:** crates/apollo_versioned_constants/resources/orchestrator_versioned_constants_0_14_2.json (L1-9)
```json
{
    "fee_proposal_margin_ppt": 2,
    "fee_proposal_window_size": 10,
    "gas_price_max_change_denominator": 48,
    "gas_target": 1500000000,
    "max_block_size": 5800000000,
    "min_gas_price": "0x1dcd65000",
    "l1_gas_price_margin_percent": 10
}
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L86-140)
```rust
pub fn calculate_next_base_gas_price(
    price: GasPrice,
    gas_used: GasAmount,
    gas_target: GasAmount,
    min_gas_price: GasPrice,
) -> GasPrice {
    let versioned_constants = VersionedConstants::latest_constants();
    assert!(
        gas_target < versioned_constants.max_block_size,
        "Gas target must be lower than max block size."
    );
    assert!(gas_target.0 > 0, "Gas target must be greater than zero.");
    assert!(
        versioned_constants.gas_price_max_change_denominator > 0,
        "Denominator constant must be greater than zero."
    );

    // If the current price is below the minimum, apply a gradual adjustment and return early.
    // This allows the price to increase by at most 1/MIN_GAS_PRICE_INCREASE_DENOMINATOR per block.
    if price < min_gas_price {
        let max_increase = price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR;
        let adjusted = price.0 + max_increase;
        // Cap at min_gas_price to avoid overshooting
        let adjusted_price = adjusted.min(min_gas_price.0);
        info!(
            "Fee Market: Price {} below minimum gas price {}, adjusted price: {} )",
            price.0, min_gas_price.0, adjusted_price
        );
        return GasPrice(adjusted_price);
    }

    // Use U256 to avoid overflow, as multiplying a u128 by a u64 remains within U256 bounds.
    let gas_delta = U256::from(gas_used.0.abs_diff(gas_target.0));
    let gas_target_u256 = U256::from(gas_target.0);
    let price_u256 = U256::from(price.0);

    // Calculate price change by multiplying first, then dividing. This avoids the precision loss
    // that occurs when dividing before multiplying.
    let denominator =
        gas_target_u256 * U256::from(versioned_constants.gas_price_max_change_denominator);
    let price_change = (price_u256 * gas_delta) / denominator;

    let adjusted_price_u256 =
        if gas_used > gas_target { price_u256 + price_change } else { price_u256 - price_change };

    // Sanity check: ensure direction of change is correct
    assert!(
        gas_used > gas_target && adjusted_price_u256 >= price_u256
            || gas_used <= gas_target && adjusted_price_u256 <= price_u256
    );

    // Price should not realistically exceed u128::MAX, bound to avoid theoretical overflow.
    let adjusted_price = u128::try_from(adjusted_price_u256).unwrap_or(u128::MAX);
    GasPrice(max(adjusted_price, min_gas_price.0))
}
```

**File:** crates/apollo_node/resources/config_schema.json (L102-106)
```json
  "batcher_config.static_config.block_builder_config.bouncer_config.block_max_capacity.receipt_l2_gas": {
    "description": "An upper bound on the total receipt-based L2 gas in a block. Includes execution gas plus state allocation costs. Should equal max_block_size.",
    "privacy": "Public",
    "value": 5800000000
  },
```

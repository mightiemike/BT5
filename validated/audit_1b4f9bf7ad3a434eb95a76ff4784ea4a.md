### Title
Gas Price Permanently Frozen at Zero Due to Integer Division Truncation in `calculate_next_base_gas_price` - (File: `crates/apollo_consensus_orchestrator/src/fee_market/mod.rs`)

---

### Summary

In `calculate_next_base_gas_price`, the "below-minimum" recovery branch computes `max_increase = price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR`. When `price.0 < 333` (the denominator), integer division truncates to `0`, so `adjusted = price.0 + 0 = price.0`, and the function returns the same price unchanged. The L2 gas price is permanently frozen and can never recover toward `min_gas_price`. This is the direct sequencer analog of the WoofiPool gamma=0 bug: a value that should be non-zero rounds down to zero via integer truncation, silently skipping the price update.

---

### Finding Description

`calculate_next_base_gas_price` in `crates/apollo_consensus_orchestrator/src/fee_market/mod.rs` implements an EIP-1559-style fee market. When `price < min_gas_price`, it enters a gradual-recovery branch:

```rust
// crates/apollo_consensus_orchestrator/src/fee_market/mod.rs lines 105-114
if price < min_gas_price {
    let max_increase = price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR;  // = price.0 / 333
    let adjusted = price.0 + max_increase;
    let adjusted_price = adjusted.min(min_gas_price.0);
    return GasPrice(adjusted_price);
}
```

`MIN_GAS_PRICE_INCREASE_DENOMINATOR` is hardcoded to `333`.

For any `price.0` in `[0, 332]`:
- `max_increase = price.0 / 333 = 0` (integer truncation)
- `adjusted = price.0 + 0 = price.0`
- `adjusted_price = min(price.0, min_gas_price.0) = price.0`
- Returns `GasPrice(price.0)` — **identical to the input**

The price is permanently frozen. No number of blocks can move it toward `min_gas_price`.

The most severe case is `price = GasPrice(0)` — the `Default` value for `GasPrice`. If `SequencerConsensusContext.l2_gas_price` is ever `0` when `update_l2_gas_price` is called (e.g., before bootstrap completes, or after a revert/sync failure), every subsequent call to `calculate_next_base_gas_price` returns `GasPrice(0)` indefinitely.

The versioned constants confirm the minimum gas price is `0x1dcd65000` = 8,000,000,000 FRI (8 Gwei) for all versions ≥ 0.14.1, which is far above 333, so the recovery branch is the only path that can produce a price in `[0, 332]`. Once entered with such a value, the branch loops forever at that value.

No existing test covers `price ∈ [0, 332]`. The test `test_calculate_with_price_below_minimum` uses `price = GasPrice(10_000_000_000)` (well above 333), and `test_gas_price_with_extreme_values` uses `price = min_gas_price` (not below it).

---

### Impact Explanation

**For `price = GasPrice(0)`:**
`convert_to_sn_api_block_info` calls `NonzeroGasPrice::new(init.l2_gas_price_fri)`, which returns an error for `GasPrice(0)`. Every block proposal built or validated with this price fails. The sequencer cannot make progress — a liveness halt.

**For `price ∈ [1, 332]`:**
`NonzeroGasPrice::new` succeeds. Blocks are built and accepted with an L2 gas price of 1–332 FRI (effectively zero). The gateway's threshold check computes `threshold = (percentage * price).to_integer()`, which truncates to `0` for any `percentage < 100` when `price ≤ 332`. Transactions with `max_price_per_unit = 0` pass the threshold check and are admitted to the mempool. The fee market is completely broken: the sequencer collects no meaningful fees, and the economic invariant that gas price reflects congestion is violated.

This matches the allowed Critical impact: *"Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact."*

---

### Likelihood Explanation

`GasPrice` derives `Default` as `GasPrice(0)`. `SequencerConsensusContext` initializes `l2_gas_price` from synced state or bootstrap. If bootstrap is skipped (e.g., sync failure, revert, or the context is constructed without running `set_height_and_round` first) and `update_l2_gas_price` is called with the default `GasPrice(0)`, the freeze is triggered. The test `test_first_height_uses_configured_min_l2_gas_price_for_height` shows bootstrap sets the price to the configured minimum — but only on the first height. A node that restarts mid-chain without syncing, or that experiences a revert to height 0, can reach this state. The bug is also silently present for any `price ∈ [1, 332]` that could arise from a misconfigured or adversarially crafted sync block.

---

### Recommendation

Replace the truncating division with a minimum-of-1 guarantee so the price always makes at least 1 FRI of progress per block when below the minimum:

```rust
// crates/apollo_consensus_orchestrator/src/fee_market/mod.rs
if price < min_gas_price {
    let max_increase = (price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR).max(1);
    let adjusted = price.0.saturating_add(max_increase);
    let adjusted_price = adjusted.min(min_gas_price.0);
    return GasPrice(adjusted_price);
}
```

Additionally, add a test case for `price = GasPrice(0)` and `price = GasPrice(332)` to the `fee_market` test suite.

---

### Proof of Concept

The following two test cases demonstrate the freeze, using the existing public API:

```rust
// Add to crates/apollo_consensus_orchestrator/src/fee_market/test.rs

#[test]
fn test_price_zero_is_permanently_frozen() {
    // GasPrice(0) is the Default value; if l2_gas_price starts here,
    // the recovery branch returns 0 forever.
    let min_gas_price = GasPrice(8_000_000_000); // 8 Gwei, from versioned constants
    let price = GasPrice(0);
    let gas_used  = GasAmount(1_000);
    let gas_target = GasAmount(2_000);

    let result = calculate_next_base_gas_price(price, gas_used, gas_target, min_gas_price);

    // Bug: returns GasPrice(0) instead of GasPrice(1) or higher.
    // max_increase = 0 / 333 = 0; adjusted = 0; adjusted_price = min(0, 8e9) = 0.
    assert_eq!(result, GasPrice(0),
        "price stuck at 0 — should have increased toward min_gas_price");
}

#[test]
fn test_price_332_is_permanently_frozen() {
    // Any price in [1, 332] is also frozen: 332 / 333 = 0.
    let min_gas_price = GasPrice(8_000_000_000);
    let price = GasPrice(332);
    let gas_used  = GasAmount(1_000);
    let gas_target = GasAmount(2_000);

    let result = calculate_next_base_gas_price(price, gas_used, gas_target, min_gas_price);

    // Bug: returns GasPrice(332) unchanged.
    assert_eq!(result, GasPrice(332),
        "price stuck at 332 — should have increased toward min_gas_price");
}
```

**Numeric walkthrough for `price = GasPrice(0)`:**

| Step | Expression | Value |
|------|-----------|-------|
| Branch condition | `0 < 8_000_000_000` | `true` → enters recovery |
| `max_increase` | `0 / 333` | `0` (truncated) |
| `adjusted` | `0 + 0` | `0` |
| `adjusted_price` | `min(0, 8_000_000_000)` | `0` |
| Return | `GasPrice(0)` | **same as input** |

Every subsequent block repeats this calculation identically. The price never moves. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L15-21)
```rust
// Denominator for the maximum gas price increase per block when price is below minimum.
// This controls how quickly the gas price can rise towards the minimum.
//
// With a denominator of 333: Each block can increase by at most 0.3% of the current price, to
// double the price takes approximately 230 blocks (at 2.6 seconds per block), this means doubling
// in approximately 10 minutes.
const MIN_GAS_PRICE_INCREASE_DENOMINATOR: u128 = 333;
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L103-115)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/fee_market/test.rs (L185-203)
```rust
#[test]
fn test_calculate_with_price_below_minimum() {
    let min_gas_price = GasPrice(20_000_000_000);
    let price = GasPrice(10_000_000_000); // Below minimum
    let gas_used = GasAmount(1000);
    let gas_target = GasAmount(2000);

    let result = calculate_next_base_gas_price(price, gas_used, gas_target, min_gas_price);

    // When price < min_gas_price, should apply gradual adjustment
    // Price increases by at most 1/MIN_GAS_PRICE_INCREASE_DENOMINATOR per block
    let max_increase = price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR;
    let expected = price.0 + max_increase;
    assert_eq!(result, GasPrice(expected));

    // Verify the increase is gradual (about 0.3% for denominator=333)
    assert!(result.0 > price.0);
    assert!(result.0 < min_gas_price.0); // Should not jump to minimum immediately
}
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L304-328)
```rust
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
```

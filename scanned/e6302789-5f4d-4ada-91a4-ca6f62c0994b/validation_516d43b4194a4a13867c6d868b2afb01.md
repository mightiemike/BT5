### Title
`OracleValueStopLossExtension` Uses Arithmetic Mean Instead of Geometric Mean for Mid Price, Systematically Miscalibrating the Stop-Loss Guard — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the mid price as the arithmetic mean of bid and ask, while the pool's canonical mid price is the geometric mean (as defined by `SwapMath.midAndSpreadFeeX64FromBidAsk`). Because AM ≥ GM for all positive bid/ask pairs, the stop-loss always uses an inflated mid price relative to the value the pool itself uses for swap execution. This deflates the token0-denominated value metric and inflates the token1-denominated value metric, causing the watermarks to be set at wrong levels. The net effect is that the stop-loss guard for token1 outflow (`zeroForOne == true`) is systematically less sensitive than the configured `drawdownE6` threshold, allowing more token1 to drain from the pool before the guard fires.

---

### Finding Description

**Wrong formula — line 218:**

```solidity
// OracleValueStopLossExtension.sol:218
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;  // arithmetic mean
```

**Correct formula used everywhere else in the protocol:**

```solidity
// SwapMath.sol:70 — the pool's canonical mid price
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);  // geometric mean
```

`PriceVelocityGuardExtension` — the sibling extension in the same directory — explicitly calls `SwapMath.midAndSpreadFeeX64FromBidAsk` and even comments *"geometric mid of two uint128 bid/ask quotes"*:

```solidity
// PriceVelocityGuardExtension.sol:48-49
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
// casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128
```

**Why AM > GM matters here:**

For symmetric oracle quotes `bid = mid·(1−s)`, `ask = mid·(1+s)`:

| Formula | Value |
|---|---|
| Arithmetic mean | `mid` (exact) |
| Geometric mean | `mid · √(1−s²)` < `mid` |

The stop-loss uses a mid price that is strictly higher than the pool's mid price whenever `s > 0`.

**Effect on the value metrics** (`_metrics`, lines 254–255):

```
metricT0 = t0/shares + (t1 / midPriceX64) / shares   ← t1 component divided by inflated mid → deflated
metricT1 = (t0 * midPriceX64) / shares + t1/shares   ← t0 component multiplied by inflated mid → inflated
```

**Effect on watermarks** (`_checkAndUpdateWatermarks`, lines 270–284):

- Watermark for token0 (`hwm0`) is set to the deflated `metricT0` → the drawdown floor `hwm0 * floorMultiplier / E6` is lower than it should be → the stop-loss for `zeroForOne == true` (token1 outflow) is **less sensitive** than configured.
- Watermark for token1 (`hwm1`) is set to the inflated `metricT1` → the floor is higher → the stop-loss for `zeroForOne == false` (token0 outflow) is **more sensitive** than configured.

The asymmetry is permanent and systematic: every watermark update compounds the miscalibration.

---

### Impact Explanation

The `OracleValueStopLossExtension` is the primary on-chain mechanism protecting LP principal from value drain. Its invariant (stated in the contract NatSpec) is:

> *"value per share at oracle marks cannot fall faster than drawdown (one-time) + decay × t (ongoing)"*

With the arithmetic mean, the effective drawdown threshold for token1 outflow is:

```
effective_drawdown ≈ drawdownE6 + s² · E6
```

where `s` is the oracle spread fraction. For a configured 5 % drawdown (`drawdownE6 = 50_000`) and a 10 % oracle spread (`s = 0.05`):

```
effective_drawdown ≈ 5 % + 0.25 % = 5.25 %
```

The guard fires 0.25 % later than intended. For a pool with $10 M of LP assets, this is $25,000 of additional token1 that can be extracted before the stop-loss triggers. The discrepancy scales with `s²`, so it grows during high-volatility periods when the oracle spread widens — exactly when the stop-loss protection is most needed.

---

### Likelihood Explanation

The bug is always active whenever `bidPriceX64 ≠ askPriceX64` (i.e., any non-zero spread). No special attacker action is required; any ordinary swap through a pool with this extension attached will set watermarks using the wrong mid price. The miscalibration is permanent and accumulates across every watermark update.

---

### Recommendation

Replace the arithmetic mean with `SwapMath.midAndSpreadFeeX64FromBidAsk`, consistent with the pool's canonical mid price and with `PriceVelocityGuardExtension`:

```solidity
// Before (wrong):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(
    uint256(bidPriceX64),
    uint256(askPriceX64)
);
```

This ensures the stop-loss evaluates bin value at the same mid price the pool uses for swap execution, making the configured `drawdownE6` threshold exact.

---

### Proof of Concept

**Setup:** Pool with 1,000 token0 and 1,000 token1 per bin, 1,000 shares. Oracle: `bid = 0.9·Q64`, `ask = 1.1·Q64` (10 % spread). Configured drawdown = 5 % (`drawdownE6 = 50_000`).

**Step 1 — First swap sets watermarks:**

```
AM mid  = (0.9 + 1.1)/2 · Q64 = 1.0 · Q64
GM mid  = √(0.9 · 1.1) · Q64 ≈ 0.9950 · Q64

metricT0 (AM) = t0/shares + t1·Q64/(1.0·Q64)/shares = 1000 + 1000 = 2000
metricT0 (GM) = t0/shares + t1·Q64/(0.995·Q64)/shares ≈ 1000 + 1005 = 2005

hwm0 (AM) = 2000   ← set too low
hwm0 (GM) = 2005   ← correct
```

**Step 2 — Token1 drains; stop-loss check:**

```
floor (AM) = 2000 · 0.95 = 1900
floor (GM) = 2005 · 0.95 = 1904.75
```

A metricT0 of 1902 would trigger the GM-based guard but **not** the AM-based guard. The pool continues to allow token1 outflow, extracting additional value beyond the 5 % drawdown limit.

**Key code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L218-218)
```text
    uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L246-256)
```text
  function _metrics(uint104 t0, uint104 t1, uint256 totalShares, uint256 minShares, uint256 midPriceX64)
    private
    pure
    returns (uint256 metricT0, uint256 metricT1)
  {
    uint256 shares = totalShares < minShares ? minShares : totalShares;
    uint256 t0ps = Math.mulDiv(uint256(t0), METRIC_SCALE, shares);
    uint256 t1ps = Math.mulDiv(uint256(t1), METRIC_SCALE, shares);
    metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L258-285)
```text
  function _checkAndUpdateWatermarks(
    address pool_,
    int8 binIdx,
    uint256 metricT0,
    uint256 metricT1,
    uint256 floorMultiplier,
    uint256 decayRate,
    bool zeroForOne
  ) private {
    BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwmS.lastDecayTs;

    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
    }

    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token0 = uint104(hwm0);
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token1 = uint104(hwm1);
    hwmS.lastDecayTs = uint32(block.timestamp);
  }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L64-72)
```text
  /// @notice Geometric mid price (Q64.64) and spread fee in Q64.64 from bid/ask oracle quotes.
  function midAndSpreadFeeX64FromBidAsk(uint256 bidPriceX64, uint256 askPriceX64)
    internal
    pure
    returns (uint256 midPriceX64, uint256 baseFeeX64)
  {
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
    baseFeeX64 = Math.mulDiv(askPriceX64, ONE_X64, midPriceX64, Math.Rounding.Ceil) - ONE_X64;
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L48-51)
```text
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);
```

Looking at the exact code path in `_afterSwapOracleStopLoss` and `_checkAndUpdateWatermarks`.

The code is fully readable. Let me trace the exact invariant break.

**Core observation — `_afterSwapOracleStopLoss` line 238:**

```solidity
if (totalShares == 0) continue;   // watermark AND lastDecayTs are never touched
```

When a bin is empty the entire `_checkAndUpdateWatermarks` call is skipped, so `highWatermarks[pool][binIdx].{token0, token1, lastDecayTs}` all remain at their pre-drain values.

**Concrete false-trigger path (no attacker required, all public flows):**

| Step | Action | State |
|---|---|---|
| 1 | Swap traverses bin at price P1=1 | watermark W₀=200 set, `lastDecayTs=T1` |
| 2 | LP calls `removeLiquidity` → `totalShares=0` | watermark still W₀=200 |
| 3 | Any swap traverses the now-empty bin | `continue` fires; W₀=200, `lastDecayTs` still T1 |
| 4 | Oracle price moves to P2=2 | — |
| 5 | New LP adds liquidity at P2 | `totalShares>0`, t0=1000, t1=1000 |
| 6 | Next swap (`zeroForOne=true`) | metricT0 = (1000 + 500)·1e6/10000 = **150** vs floor = 200·0.9 = **180** → `OracleStopLossTriggered` |

No value was leaked; the metric drop is purely from the price move. The watermark was never updated during the empty period to reflect the new price regime.

**Why `_metrics` does not divide by zero (the skip is not needed for safety):** [1](#0-0) 

`shares` is floored at `minShares` (≥1), so `totalShares==0` would safely produce metric=0 (since t0=t1=0 in an empty bin). The skip is a design choice, not a safety guard.

**`lastDecayTs` is also frozen:** [2](#0-1) 

Because `_checkAndUpdateWatermarks` is never called for the empty bin, `lastDecayTs` is not advanced. When the bin is later re-populated, `dt = block.timestamp − lastDecayTs` is large, which helps decay the watermark — but only if `decayPerSecondE8 > 0`. With `decayPerSecondE8 == 0` (a valid configuration), the stale watermark persists indefinitely and the pool is stuck until admin intervention.

**The existing test confirms the skip is intentional but does not cover the drain-then-refill case:** [3](#0-2) 

`test_skipsEmptyBins` only verifies that a bin that was *always* empty keeps watermark=0. It never tests a bin that had a watermark set, was drained, and was then refilled.

---

### Title
Stale high-watermark after bin drain causes false `OracleStopLossTriggered` on re-populated bins — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

### Summary
`_afterSwapOracleStopLoss` skips `_checkAndUpdateWatermarks` for any bin with `totalShares == 0`. This leaves the bin's high-watermark (and its decay clock) frozen at the pre-drain value. When the bin is later re-populated at a different oracle price, the first swap through it is compared against the stale watermark and can trigger a false stop-loss, permanently blocking swaps in that direction until admin resets the watermark through a timelocked governance action.

### Finding Description
In `_afterSwapOracleStopLoss`: [4](#0-3) 

The `continue` on line 238 skips the entire `_checkAndUpdateWatermarks` call. For a bin that previously had a watermark set, this means:

1. `highWatermarks[pool][binIdx].token0` and `.token1` remain at the old high-water values.
2. `highWatermarks[pool][binIdx].lastDecayTs` is not advanced, so the decay clock is frozen.

When an LP later re-adds liquidity to the bin, the next swap computes a fresh metric against the stale watermark. If the oracle price moved significantly during the empty period, `metricT0` (or `metricT1`) at the new price can fall below `hwm * (E6 − drawdown) / E6` even though no value was leaked, causing `OracleStopLossTriggered` to revert the swap.

The skip is not required to prevent division by zero: `_metrics` already floors `shares` at `minShares`: [1](#0-0) 

With `t0 = t1 = 0` (empty bin), `_metrics` would safely return `(0, 0)`, and `_applyWatermark(0, hwm, floor)` would set `newHwm = hwm` and `breached = (0 < hwm * floor / E6)` — which is true only if `hwm > 0`. So the correct fix is to call `_checkAndUpdateWatermarks` with metric `(0, 0)` for empty bins, which resets the watermark to 0 and advances `lastDecayTs`.

### Impact Explanation
Swaps through a re-populated bin are permanently reverted with `OracleStopLossTriggered` even though no value was leaked. With `decayPerSecondE8 == 0` (valid config), the pool remains stuck in that direction until the pool admin executes a timelocked watermark reset. This constitutes broken core swap functionality.

### Likelihood Explanation
The sequence — LP removes all liquidity, price moves, new LP re-adds liquidity — is a routine lifecycle event requiring no malicious actor. Any pool with a non-trivial drawdown threshold and a price-sensitive oracle is exposed whenever a bin is fully drained.

### Recommendation
Remove the `continue` and instead call `_checkAndUpdateWatermarks` with `(metricT0, metricT1) = (0, 0)` for empty bins. This resets the watermark to 0 and advances `lastDecayTs`, so the next re-population ratchets the watermark up from zero with no false breach:

```solidity
for (uint256 i = 0; i < count; i++) {
    uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
    uint256 metricT0;
    uint256 metricT1;
    if (totalShares > 0) {
        (uint104 t0, uint104 t1,,,) = PoolStateLibrary._decodeBinState(states[i]);
        (metricT0, metricT1) = _metrics(t0, t1, totalShares, minShares, midPriceX64);
    }
    // metricT0 == metricT1 == 0 for empty bins: resets watermark to 0, advances decay clock
    _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
}
```

Note: `_checkAndUpdateWatermarks` with metric=0 and a non-zero watermark will set `breached=true`. The revert guard must therefore be conditioned on `totalShares > 0` as well, or the empty-bin path must bypass the revert but still write `hwmS.token0 = 0; hwmS.token1 = 0; hwmS.lastDecayTs = block.timestamp`.

### Proof of Concept
```solidity
function test_stalWatermark_falseStopLoss_afterDrainAndRefill() public {
    uint128 priceP1 = uint128(Q64);       // mid = 1
    uint128 priceP2 = uint128(2 * Q64);   // mid = 2

    // 1. Populate bin, set watermark at P1
    _storeBin(0, 1000, 1000, BIN_SHARES);
    _configure(100_000, 0);               // 10% drawdown, no decay
    _exposeStopLoss(0, 0, priceP1, false);

    // 2. Drain bin (LP removes all liquidity)
    _storeBin(0, 0, 0, 0);

    // 3. Swap traverses empty bin — watermark frozen at P1 value
    _exposeStopLoss(0, 0, priceP2, false);

    // 4. New LP re-adds liquidity at P2
    _storeBin(0, 1000, 1000, BIN_SHARES);

    // 5. Next swap — false stop-loss: metricT0 at P2 < floor of stale watermark
    vm.expectRevert();
    _exposeStopLoss(0, 0, priceP2, true);
}
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L236-242)
```text
    for (uint256 i = 0; i < count; i++) {
      uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
      if (totalShares == 0) continue;
      (uint104 t0, uint104 t1,,,) = PoolStateLibrary._decodeBinState(states[i]);
      (uint256 metricT0, uint256 metricT1) = _metrics(t0, t1, totalShares, minShares, midPriceX64);
      _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
    }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L251-251)
```text
    uint256 shares = totalShares < minShares ? minShares : totalShares;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L280-284)
```text
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token0 = uint104(hwm0);
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token1 = uint104(hwm1);
    hwmS.lastDecayTs = uint32(block.timestamp);
```

**File:** metric-periphery/test/extensions/OracleValueStopLossSubExtension.t.sol (L659-674)
```text
  function test_skipsEmptyBins() public {
    uint128 price = uint128(Q64);
    _storeBin(0, 1000, 1000, BIN_SHARES);
    _storeBin(1, 0, 0, 0);
    _storeBin(2, 1000, 1000, BIN_SHARES);
    _configure(50_000, 0);

    _exposeStopLoss(0, 2, price, false);

    (uint256 hwm0,) = extension.currentHighWatermarks(address(mockPool), 0);
    (uint256 hwm1,) = extension.currentHighWatermarks(address(mockPool), 1);
    (uint256 hwm2,) = extension.currentHighWatermarks(address(mockPool), 2);
    assertGt(hwm0, 0);
    assertEq(hwm1, 0);
    assertGt(hwm2, 0);
  }
```

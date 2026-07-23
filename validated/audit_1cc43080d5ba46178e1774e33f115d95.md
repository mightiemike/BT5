Audit Report

## Title
`OracleValueStopLossExtension` Stop-Loss Bypassed via Stale `lastDecayTs` When Bin Transitions from Empty to Active — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

`_afterSwapOracleStopLoss` skips bins with zero total shares via an unconditional `continue`, leaving `lastDecayTs` frozen at the last active timestamp. When a bin drains to empty and later receives liquidity again, the elapsed `dt` spans the entire dormant period, causing `_decayed` to collapse the watermark to zero. With a zero watermark, `_applyWatermark` always returns `breached = false`, permanently disabling the stop-loss for that bin until the admin manually resets it.

## Finding Description

The empty-bin skip at [1](#0-0)  causes the loop to `continue` without ever calling `_checkAndUpdateWatermarks`, which is the only site that writes `hwmS.lastDecayTs`: [2](#0-1) 

The other write to `lastDecayTs` is in `executeOracleStopLossHighWatermarks`, which sets it to `block.timestamp` at admin-configuration time: [3](#0-2) 

When the bin later becomes active, `_checkAndUpdateWatermarks` computes `dt` as the raw difference from the frozen timestamp: [4](#0-3) 

`_decayed` returns 0 whenever `ratePerSecondE8 * dt >= 1e8`: [5](#0-4) 

With `hwm = 0`, `_applyWatermark`'s first branch `metric >= hwm` is always satisfied (any non-negative metric ≥ 0), so it unconditionally returns `(metric, false)`: [6](#0-5) 

The test `test_skipsEmptyBins` confirms that a bin with `shares=0` is skipped and its watermark storage is never touched: [7](#0-6) 

## Impact Explanation

The stop-loss is the primary on-chain mechanism preventing LP value from falling below the configured drawdown floor. Once the watermark collapses to zero, `_applyWatermark` never sets `breached = true`, so `OracleStopLossTriggered` is never emitted and the swap proceeds regardless of the actual oracle-derived metric. This allows an attacker to execute swaps through the bin at an unfavorable oracle price, extracting token value from LPs below the configured floor — a direct loss of LP principal meeting the "bad-price execution" and "direct loss of user principal" impact criteria.

## Likelihood Explanation

All preconditions are reachable through normal, unprivileged pool operation:
1. Pool admin (semi-trusted) configures a non-zero `decayPerSecondE8` and sets watermarks for a bin.
2. LPs remove all liquidity from that bin (routine rebalancing) — no attacker action required.
3. Swaps continue crossing adjacent bins; the empty bin is skipped each time, freezing `lastDecayTs`.
4. After `ceil(1e8 / decayPerSecondE8)` seconds the watermark is fully decayed. At `decayPerSecondE8 = 58` (~5%/day) this threshold is ~20 days.
5. Any LP re-adds liquidity to the bin (also routine).
6. An unprivileged attacker executes a swap through the bin; the stop-loss does not fire.

No privileged access or malicious setup is required beyond waiting for natural LP behaviour.

## Recommendation

Inside `_afterSwapOracleStopLoss`, when a bin is detected as empty (`totalShares == 0`), still advance the decay clock without touching the watermark values:

```solidity
if (totalShares == 0) {
    highWatermarks[pool_][binIdxs[i]].lastDecayTs = uint32(block.timestamp);
    continue;
}
```

This ensures that when the bin becomes active again, `dt` reflects only the time since the last swap traversal, not the entire dormant period, preserving the intended decay semantics.

## Proof of Concept

```
1. Deploy pool with OracleValueStopLossExtension, decayPerSecondE8 = 58.
2. Admin calls executeOracleStopLossHighWatermarks(pool, binIdx=1, hwm0=1000, hwm1=1000).
   → highWatermarks[pool][1] = {token0:1000, token1:1000, lastDecayTs: T0}
3. All LPs remove liquidity from bin 1 → totalShares[1] = 0.
4. Swaps cross bins 0 and 2 for 20+ days; bin 1 is skipped each time (continue at line 238).
   → lastDecayTs for bin 1 remains T0.
5. LP adds liquidity back to bin 1 at time T0 + 1_728_000s (20 days).
6. Attacker calls swap crossing bin 1.
   → dt = 1_728_000s
   → factor = 58 * 1_728_000 = 100_224_000 >= 1e8
   → _decayed(1000, 58, 1_728_000) = 0
   → _applyWatermark(metricT0, 0, floorMultiplier): metric >= 0 → returns (metric, false)
   → OracleStopLossTriggered is never emitted; swap proceeds.
7. Attacker drains LP value from bin 1 below the configured drawdown floor.
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L173-174)
```text
    highWatermarks[pool_][pending.binIdx] =
      BinHighWatermarks({token0: pending.token0, token1: pending.token1, lastDecayTs: uint32(block.timestamp)});
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L237-238)
```text
      uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
      if (totalShares == 0) continue;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L267-268)
```text
    BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwmS.lastDecayTs;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L284-284)
```text
    hwmS.lastDecayTs = uint32(block.timestamp);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L319-323)
```text
  function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L333-333)
```text
    if (metric >= hwm) return (metric, false);
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

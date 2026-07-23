### Title
`OracleValueStopLossExtension` Stop-Loss Bypassed via Stale `lastDecayTs` When Bin Transitions from Empty to Active — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`_afterSwapOracleStopLoss` skips empty bins entirely, leaving their `lastDecayTs` unchanged. When a bin that previously held admin-configured watermarks becomes empty and then receives liquidity again, the elapsed time (`dt`) used in the decay calculation is inflated by the entire empty period. With a non-zero decay rate this causes the watermark to collapse to zero on the first post-reactivation swap, permanently disabling the stop-loss for that bin until the admin manually resets it.

---

### Finding Description

`_checkAndUpdateWatermarks` is the only place that writes `hwmS.lastDecayTs`: [1](#0-0) 

It is only reached when the bin is non-empty. The test `test_skipsEmptyBins` confirms that a bin with `t0=0, t1=0, shares=0` is skipped and its watermark storage is never touched: [2](#0-1) 

The decay calculation uses the raw difference: [3](#0-2) 

`_decayed` fully collapses the watermark to zero when `ratePerSecondE8 * dt >= 1e8`: [4](#0-3) 

`executeOracleStopLossHighWatermarks` sets `lastDecayTs = block.timestamp` at execution time: [5](#0-4) 

After that, if the bin drains to zero and swaps continue to cross other bins, `lastDecayTs` for the empty bin is frozen at the admin-set timestamp. When liquidity returns and the next swap touches the bin, `dt` spans the entire dormant period, collapsing the watermark to zero and making `_applyWatermark` report no breach regardless of the actual metric value.

---

### Impact Explanation

The stop-loss is the primary on-chain mechanism preventing LP value from being drained below the configured drawdown floor. When the watermark collapses to zero, `_applyWatermark` always returns `breached = false`: [6](#0-5) 

A swap that would otherwise be blocked by `OracleStopLossTriggered` proceeds, allowing the attacker to extract token value from the bin at an unfavorable oracle price. This is a direct loss of LP principal.

---

### Likelihood Explanation

The conditions are reachable through normal pool operation:

1. Pool admin configures a non-zero `decayPerSecondE8` and sets watermarks for a bin via `executeOracleStopLossHighWatermarks`.
2. LPs remove all liquidity from that bin (routine rebalancing).
3. Swaps continue to cross adjacent bins; the empty bin is skipped each time, freezing its `lastDecayTs`.
4. After `ceil(1e8 / decayPerSecondE8)` seconds the watermark is fully decayed. At `decayPerSecondE8 = 58` (~5 %/day) this threshold is ~20 days.
5. LPs re-add liquidity to the bin (also routine).
6. An attacker executes a swap through the bin; the stop-loss does not fire.

No privileged access or malicious setup is required beyond waiting for natural LP behaviour.

---

### Recommendation

Update `lastDecayTs` for every bin in the traversal range, even when the bin is empty, so the decay clock advances continuously regardless of bin activity. Concretely, inside `_afterSwapOracleStopLoss`, when a bin is detected as empty, still write `highWatermarks[pool_][binIdx].lastDecayTs = uint32(block.timestamp)` without touching the watermark values themselves. This mirrors the IonPool fix: always advance the timestamp so that the next active period starts from the correct baseline.

---

### Proof of Concept

```
1. Deploy pool with OracleValueStopLossExtension, decayPerSecondE8 = 58.
2. Admin calls executeOracleStopLossHighWatermarks(pool, binIdx=1, hwm0=1000, hwm1=1000).
   → highWatermarks[pool][1] = {token0:1000, token1:1000, lastDecayTs: T0}
3. All LPs remove liquidity from bin 1 → bin 1 is empty.
4. Swaps cross bins 0 and 2 for 20+ days; bin 1 is skipped each time.
   → lastDecayTs for bin 1 remains T0.
5. LP adds liquidity back to bin 1 at time T0 + 20 days.
6. Attacker calls swap crossing bin 1.
   → dt = 20 days = 1_728_000 s
   → factor = 58 * 1_728_000 = 100_224_000 >= 1e8
   → _decayed(1000, 58, 1_728_000) = 0
   → _applyWatermark(metricT0, 0, floorMultiplier) → breached = false
   → OracleStopLossTriggered is never emitted; swap proceeds.
7. Attacker drains LP value from bin 1 below the configured drawdown floor.
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L173-174)
```text
    highWatermarks[pool_][pending.binIdx] =
      BinHighWatermarks({token0: pending.token0, token1: pending.token1, lastDecayTs: uint32(block.timestamp)});
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L267-268)
```text
    BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwmS.lastDecayTs;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L280-284)
```text
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token0 = uint104(hwm0);
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token1 = uint104(hwm1);
    hwmS.lastDecayTs = uint32(block.timestamp);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L319-324)
```text
  function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L328-336)
```text
  function _applyWatermark(uint256 metric, uint256 hwm, uint256 floorMultiplier)
    private
    pure
    returns (uint256 newHwm, bool breached)
  {
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);
  }
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

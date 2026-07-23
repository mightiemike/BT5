The code is fully available. Let me trace the exact logic carefully.

The full code is available. I can now trace the exact execution path and make a definitive determination.

**Exact trace through `_checkAndUpdateWatermarks` when `breach0=true`, `zeroForOne=false`:**

1. `dt = block.timestamp - hwmS.lastDecayTs` — some elapsed time [1](#0-0) 
2. `_applyWatermark` for token0: since `metricT0 < hwm`, it returns `(hwm_old, true)` — the **old watermark value is preserved**, not replaced by the lower metric [2](#0-1) 
3. `if (breach0 && zeroForOne)` — `zeroForOne=false`, so **no revert** [3](#0-2) 
4. `hwmS.token0 = uint104(hwm0)` — old high watermark written back unchanged [4](#0-3) 
5. `hwmS.lastDecayTs = uint32(block.timestamp)` — **decay clock reset unconditionally** [5](#0-4) 

The result: `hwmS.token0` stays at the old high watermark AND `lastDecayTs` is reset to now. On the next zeroForOne attempt, `dt` is near zero, `_decayed(hwmS.token0, ...)` returns a value close to the old high, the breach is re-detected, and the swap reverts again.

Any token1-direction swap — by anyone, including normal users — repeats this reset. The watermark never accumulates enough elapsed time to decay below the floor.

The existing test `test_decayRearmsAfterPermanentRepricing` (line 439) only works because it warps time with **no intervening swaps**. In a live pool with any token1-direction activity, the clock is continuously reset and the zeroForOne direction stays blocked indefinitely. [6](#0-5) 

---

### Title
Unconditional `lastDecayTs` reset in `_checkAndUpdateWatermarks` allows any token1-direction swap to permanently suppress watermark decay, indefinitely blocking zeroForOne swaps — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

### Summary
`_checkAndUpdateWatermarks` resets `hwmS.lastDecayTs = block.timestamp` unconditionally on every call, even when `breach0=true` and `zeroForOne=false`. Because `_applyWatermark` preserves the old high watermark when a breach exists, the watermark value is never lowered, but the decay clock is reset to now. Any subsequent token1-direction swap repeats this, keeping `dt` perpetually near zero and preventing the watermark from ever decaying below the drawdown floor. The zeroForOne direction remains blocked indefinitely without admin intervention.

### Finding Description
The decay mechanism is designed to allow automatic recovery: over time, `_decayed(hwmS.token0, decayRate, dt)` falls until it drops below `metricT0`, at which point `_applyWatermark` ratchets the watermark down to the live metric and the breach clears. This requires `dt` to grow large enough.

However, line 284 resets `lastDecayTs` on every successful call regardless of breach state:

```solidity
hwmS.token0 = uint104(hwm0);   // old high preserved when breach0=true
hwmS.token1 = uint104(hwm1);
hwmS.lastDecayTs = uint32(block.timestamp);  // clock reset unconditionally
```

When `breach0=true` and `zeroForOne=false`:
- `hwm0` = old high watermark (from `_applyWatermark` returning `(hwm, true)`)
- `hwmS.token0` is written back unchanged
- `lastDecayTs` is reset to now

The next zeroForOne swap computes `dt = block.timestamp - lastDecayTs` ≈ 0, so `_decayed` returns the full old watermark, the breach is re-detected, and the swap reverts. Any token1-direction swap — by any user — repeats this cycle.

### Impact Explanation
zeroForOne swaps are permanently blocked in any pool where:
1. A genuine breach condition exists (`metricT0 < hwm0 * floorMultiplier / E6`)
2. Any token1-direction trading activity occurs (normal market behavior)

This breaks core swap functionality for one direction of the pool. The only recovery path is the pool admin manually proposing and executing new watermarks via `proposeOracleStopLossHighWatermarks` / `executeOracleStopLossHighWatermarks`, which requires waiting through the timelock. The automatic decay recovery — the mechanism explicitly designed for this scenario — is rendered inoperative.

### Likelihood Explanation
The breach condition arises naturally from oracle mid-price moves (a mid spike causes `metricT0` to drop). Once triggered, any normal token1-direction swap (e.g., arbitrageurs correcting the price back) resets the clock. No special attacker setup is required; ordinary pool usage is sufficient to keep the clock reset.

### Recommendation
Only reset `lastDecayTs` when no breach exists in either direction, or maintain separate decay timestamps per direction:

```solidity
// Only advance the decay clock when neither direction is breached
if (!breach0 && !breach1) {
    hwmS.lastDecayTs = uint32(block.timestamp);
}
```

Alternatively, track `lastDecayTs0` and `lastDecayTs1` independently and only reset each when the corresponding metric is not in breach.

### Proof of Concept
```solidity
// Setup: pool with drawdown=50%, decay=58 E8/s (~5%/day)
// 1. Establish watermark at metricT0 = 200 (e.g., 1000 shares, 1000/1000 balances)
// 2. Price spikes: metricT0 drops to 80 (below floor of 100 = 200 * 50%)
// 3. zeroForOne swap reverts — breach confirmed
// 4. Execute token1-direction swap (zeroForOne=false) — passes, resets lastDecayTs
// 5. Warp 5 days — would normally be enough for decay to clear
// 6. Execute another token1-direction swap — resets lastDecayTs again to now
// 7. zeroForOne swap still reverts — dt is near 0, watermark undecayed
// 8. Repeat step 6 indefinitely — pool permanently blocked in zeroForOne direction
assertEq(hwmS.lastDecayTs, block.timestamp); // clock reset by step 6
// zeroForOne swap reverts despite 5+ days having passed since breach
```

The test `test_decayRearmsAfterPermanentRepricing` passes only because it warps time with no intervening swaps — a condition that does not hold in a live pool. [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L267-268)
```text
    BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwmS.lastDecayTs;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L271-273)
```text
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L281-281)
```text
    hwmS.token0 = uint104(hwm0);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L284-284)
```text
    hwmS.lastDecayTs = uint32(block.timestamp);
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

**File:** metric-periphery/test/extensions/OracleValueStopLossSubExtension.t.sol (L439-461)
```text
  function test_decayRearmsAfterPermanentRepricing() public {
    uint128 price = uint128(Q64);
    _storeBin(0, 1000, 1000, BIN_SHARES);
    _configure(50_000, 58); // ~5%/day

    _exposeStopLoss(0, 0, price, false);

    _storeBin(0, 800, 800, BIN_SHARES);

    vm.expectRevert();
    _exposeStopLoss(0, 0, price, true);

    // Warp until decayed watermark ratchets below the drawdown floor (~4 days at 58 E8/s).
    vm.warp(block.timestamp + 5 days);

    _exposeStopLoss(0, 0, price, true);

    (uint256 hwm0, uint256 hwm1) = extension.currentHighWatermarks(address(mockPool), 0);
    uint256 cur0 = _computeMetricToken0(800, 800, BIN_SHARES, price);
    uint256 cur1 = _computeMetricToken1(800, 800, BIN_SHARES, price);
    assertGe(hwm0, cur0);
    assertGe(hwm1, cur1);
  }
```

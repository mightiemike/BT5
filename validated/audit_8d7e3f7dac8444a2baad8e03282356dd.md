### Title
`_validateDrawdown` accepts `drawdownE6 = E6`, zeroing `floorMultiplier` and permanently disabling the `OracleValueStopLossExtension` stop-loss guard — (`File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`_validateDrawdown` uses a strict `>` comparison, allowing `drawdownE6 = 1e6` (100%). When this boundary value is stored, `floorMultiplier = E6 − drawdown = 0`, making the breach condition in `_applyWatermark` mathematically impossible for any `uint256` metric. The stop-loss guard is silently disabled while the extension appears fully configured and active.

---

### Finding Description

**Validation boundary (line 305–307):**

```solidity
function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
}
```

`drawdownE6 = E6 = 1e6` passes this check. The same off-by-one is present in `_validateDecay` (`if (decayPerSecondE8 > E8)`), but the drawdown path is the most direct disabling vector.

**Floor computation (line 234):**

```solidity
uint256 floorMultiplier = E6 - drawdown;   // = 1e6 - 1e6 = 0
```

**Breach condition (line 334):**

```solidity
breached = metric < (hwm * floorMultiplier) / E6;
// = metric < (hwm * 0) / 1e6
// = metric < 0          ← always false for uint256
```

`_applyWatermark` therefore always returns `breached = false`, regardless of how far the per-share metric has fallen below the high-watermark. Neither the `zeroForOne` branch (line 271) nor the `!zeroForOne` branch (line 276) ever reverts.

The intended disabling path is `drawdownE6 = 0`, which triggers an explicit early return at line 217 (`if (drawdown == 0) return;`). The `drawdownE6 = E6` path bypasses that guard, stores a non-zero drawdown that looks active, yet produces a zero floor through arithmetic — a silent misconfiguration with no on-chain signal.

The same structural defect exists for `decayPerSecondE8 = E8`: `_decayed` returns 0 whenever `factor = ratePerSecondE8 * dt ≥ E8` (line 322), so watermarks collapse to zero after one second, and `_applyWatermark` again returns `(metric, false)` because `metric ≥ 0` is always true.

---

### Impact Explanation

The `OracleValueStopLossExtension` is the sole on-chain mechanism that blocks value-draining swaps from continuing past the configured drawdown floor. With `floorMultiplier = 0`, every swap — including those that reduce per-share bin value to zero — passes the after-swap hook without reverting. LPs suffer direct, unbounded principal loss: token0 and token1 balances in affected bins can be fully extracted by a sequence of swaps while the guard silently reports no breach.

---

### Likelihood Explanation

The pool admin sets `drawdownE6` at factory initialization and can update it via the timelocked propose/execute flow. A value of `E6` (100%) is the natural "maximum" a developer might reach for when testing or when intending to set a very permissive floor, and the validation passes it without error or warning. Because `drawdownE6 = 0` is the documented disable path, an admin who sets `E6` expecting a "100% tolerance" configuration has no indication the guard is broken. The timelock on subsequent corrections means the window of exposure can span days.

---

### Recommendation

Change both validation helpers to use `>=` so the boundary values are rejected:

```solidity
function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 >= E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
}

function _validateDecay(uint256 decayPerSecondE8) private pure {
    if (decayPerSecondE8 >= E8) revert OracleStopLossDecayTooLarge(decayPerSecondE8);
}
```

Additionally, add an explicit revert in `_applyWatermark` or `_afterSwapOracleStopLoss` if `floorMultiplier == 0` and `drawdown != 0`, to catch any future path that could produce a zero floor from a non-zero drawdown.

---

### Proof of Concept

```
1. Deploy OracleValueStopLossExtension.
2. Factory calls initialize(pool, abi.encode(uint32(1e6), uint32(58), uint32(0))).
   → drawdownE6 = 1e6 passes _validateDrawdown (1e6 > 1e6 is false).
   → oracleStopLossConfig[pool].drawdownE6 = 1e6.

3. Admin sets watermarks: proposeOracleStopLossHighWatermarks(pool, 0, 1000, 1000)
   then executeOracleStopLossHighWatermarks(pool).
   → highWatermarks[pool][0] = {token0: 1000, token1: 1000, lastDecayTs: now}.

4. Attacker executes a swap that drains bin 0 to (t0=0, t1=0).
   Pool calls afterSwap → _afterSwapOracleStopLoss.

5. Inside _checkAndUpdateWatermarks:
   floorMultiplier = 1e6 - 1e6 = 0
   metricT0 = 0  (bin is empty)
   hwm0 = 1000   (watermark still set)
   _applyWatermark(0, 1000, 0):
     metric(0) < hwm(1000) → enter breach check
     breached = 0 < (1000 * 0) / 1e6 = 0 < 0 → false
     return (1000, false)
   breach0 = false → no revert.

6. Swap settles. Bin 0 is empty. LPs receive nothing.
   Stop-loss never fired.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L215-217)
```text
    PoolStopLossConfig memory cfg = oracleStopLossConfig[pool_];
    uint256 drawdown = cfg.drawdownE6;
    if (drawdown == 0) return;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L234-234)
```text
    uint256 floorMultiplier = E6 - drawdown;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L270-278)
```text
    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
    }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-307)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L309-311)
```text
  function _validateDecay(uint256 decayPerSecondE8) private pure {
    if (decayPerSecondE8 > E8) revert OracleStopLossDecayTooLarge(decayPerSecondE8);
  }
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

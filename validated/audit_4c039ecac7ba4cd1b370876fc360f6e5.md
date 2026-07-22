### Title
`uint32` Timestamp Truncation in `lastDecayTs` Causes Inflated `dt`, Permanently Disabling the Oracle Stop-Loss Guard — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension` stores `lastDecayTs` as `uint32(block.timestamp)` but computes elapsed time `dt` in unchecked `uint256` space. After year 2106, when `block.timestamp` exceeds `uint32.max`, the stored `lastDecayTs` wraps to a small value while `block.timestamp` does not, inflating `dt` by `2^32` seconds. This forces every call to `_decayed()` to return `0`, collapsing all per-bin high watermarks to zero and permanently disabling the stop-loss guard for every pool that uses this extension.

---

### Finding Description

In `_checkAndUpdateWatermarks`, the decay timestamp is written and read as follows:

```solidity
// write (line 284)
hwmS.lastDecayTs = uint32(block.timestamp);

// read (line 268)
uint256 dt = block.timestamp - hwmS.lastDecayTs;
``` [1](#0-0) [2](#0-1) 

The same pattern appears in the public view helper `currentHighWatermarks`:

```solidity
uint256 dt = block.timestamp - hwm.lastDecayTs;
``` [3](#0-2) 

`lastDecayTs` is a `uint32` field inside `BinHighWatermarks`. After year 2106, `block.timestamp > 4 294 967 295` (`uint32.max`). At that point:

- `uint32(block.timestamp)` wraps to `block.timestamp % 2^32` — a small value, e.g. `1000`.
- The subtraction `block.timestamp - uint256(lastDecayTs)` is performed in `uint256` space, so it equals `block.timestamp - 1000`, which is approximately `block.timestamp` — a value on the order of `4.3 × 10^9` seconds.

`_decayed` then computes:

```solidity
uint256 factor = ratePerSecondE8 * dt;
if (factor >= E8) return 0;
``` [4](#0-3) 

With `dt ≈ 4.3 × 10^9` and even the minimum non-zero `ratePerSecondE8 = 1`, `factor = 4.3 × 10^9 >> E8 = 10^8`, so `_decayed` always returns `0`. Every watermark collapses to zero.

`_applyWatermark` then receives `hwm = 0`:

```solidity
if (metric >= hwm) return (metric, false);
``` [5](#0-4) 

Since `metric >= 0` is always true, the function always returns `(metric, false)` — no breach is ever reported. The stop-loss guard is silently and permanently disabled.

The inconsistency mirrors the external bug exactly: the write path truncates to `uint32` (analogous to `_updateTWAV` using `unchecked`), while the read path operates in `uint256` without compensating for the wrap-around (analogous to `_getTwav` omitting `unchecked`).

---

### Impact Explanation

The `OracleValueStopLossExtension` is the primary mechanism protecting LP principal from oracle-driven value drain. Once `lastDecayTs` wraps, every `afterSwap` hook call silently passes without checking any watermark. Any swap — including one that drains a bin's value far below the configured drawdown floor — proceeds without triggering the guard. LP funds that the extension was configured to protect are exposed to unbounded loss with no on-chain recourse.

---

### Likelihood Explanation

The trigger is `block.timestamp > uint32.max`, which occurs in year 2106 — identical timing to the external reference bug. Pools deployed today and still active then are affected. The vault is not described as upgradeable in the periphery layer, so there is no automatic remediation path. The window of vulnerability is not narrow: once `block.timestamp` exceeds `uint32.max`, every subsequent `afterSwap` call is affected for the lifetime of the pool.

---

### Recommendation

Store `lastDecayTs` as `uint256` to eliminate truncation entirely:

```solidity
// In BinHighWatermarks struct: change uint32 lastDecayTs → uint256 lastDecayTs
hwmS.lastDecayTs = block.timestamp;
```

Alternatively, if storage packing requires `uint32`, mirror the Uniswap v2 pattern and compute `dt` in `uint32` space with `unchecked`:

```solidity
uint32 dt;
unchecked {
    dt = uint32(block.timestamp) - hwmS.lastDecayTs;
}
```

This makes the wrap-around arithmetic intentional and consistent on both the write and read sides, exactly as the external report's recommended fix applied `unchecked` symmetrically to both `_updateTWAV` and `_getTwav`.

---

### Proof of Concept

1. Deploy `OracleValueStopLossExtension` with a non-zero `drawdownE6` and `decayPerSecondE8`.
2. Call `executeOracleStopLossHighWatermarks` to set a non-zero watermark for a bin; `lastDecayTs` is written as `uint32(block.timestamp)`.
3. Warp `block.timestamp` to `4 294 967 297` (just past `uint32.max`). `uint32(block.timestamp) = 1`.
4. Call `afterSwap` on the pool. Inside `_checkAndUpdateWatermarks`:
   - `dt = 4 294 967 297 - 1 = 4 294 967 296`
   - `factor = ratePerSecondE8 * 4 294 967 296 >= E8` → `_decayed` returns `0`
   - `_applyWatermark(metric, 0, floor)` → `metric >= 0` → returns `(metric, false)`
5. The swap completes even though the bin's value per share has fallen below the configured drawdown floor. The stop-loss guard never fires. [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L74-75)
```text
    uint256 dt = block.timestamp - hwm.lastDecayTs;
    return (_decayed(hwm.token0, rate, dt), _decayed(hwm.token1, rate, dt));
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L321-322)
```text
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L333-333)
```text
    if (metric >= hwm) return (metric, false);
```

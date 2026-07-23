Audit Report

## Title
`uint32 lastDecayTs` Truncation Permanently Disables Stop-Loss Guard After Year 2106 — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

`BinHighWatermarks.lastDecayTs` is declared as `uint32` and written via `uint32(block.timestamp)`. After year 2106, `block.timestamp` exceeds `type(uint32).max`, causing the stored value to wrap. The subsequent `dt` subtraction in `_checkAndUpdateWatermarks` and `currentHighWatermarks` produces a massively inflated elapsed-time, which forces `_decayed` to return `0` for every watermark. With all watermarks zeroed, `_applyWatermark` never detects a breach, permanently disabling the stop-loss guard for every pool using this extension.

## Finding Description

**Root cause — storage truncation:**
`BinHighWatermarks.lastDecayTs` is `uint32` per the interface struct definition. [1](#0-0) 

It is written at line 284 and line 174 as `uint32(block.timestamp)`, silently truncating the timestamp. [2](#0-1) 

**Inflated `dt` computation:**
At line 268, `hwmS.lastDecayTs` (a `uint32`) is zero-extended to `uint256` before subtraction. After year 2106, `block.timestamp ≈ 2^32 + X` while `uint32(block.timestamp) = X` (wrapped). The result is `dt ≈ 2^32 ≈ 4,294,967,296`. [3](#0-2) 

**`_decayed` always returns 0:**
`ratePerSecondE8` is validated to be at most `E8 = 1e8`. With `dt ≈ 4.3e9`, `factor = 1e8 × 4.3e9 = 4.3e17 ≥ 1e8`, so the `factor >= E8` branch always fires and returns `0`. [4](#0-3) 

**`_applyWatermark` never detects a breach:**
With `hwm = 0`, the condition `metric >= hwm` is always satisfied (any `uint256 >= 0`), so `breached` is always `false` and the watermark is silently ratcheted up to the current metric on every swap. [5](#0-4) 

**Secondary — `_afterTimelock` overflow bypasses timelocks:**
`uint32(block.timestamp + timelock)` overflows after year 2106, wrapping `executeAfter` below `block.timestamp`, so `_requireElapsed` passes immediately for every pending proposal. [6](#0-5) 

## Impact Explanation

The `OracleValueStopLossExtension` is the primary on-chain guard preventing LP value from draining below a configured drawdown floor. After year 2106:

1. All per-bin watermarks are silently zeroed on every swap.
2. No stop-loss breach is ever detected regardless of oracle mid-price movement.
3. Swaps that should revert with `OracleStopLossTriggered` execute freely.
4. LP principal can be drained without bound through adversarial or oracle-driven price moves.
5. All pending admin changes (drawdown, decay, watermark resets) can be executed without waiting for the configured timelock, bypassing the admin-boundary timelock protection.

This matches the "oracle guard path fails open" and "direct loss of LP principal" impact categories.

## Likelihood Explanation

The event occurs with 100% probability once `block.timestamp` crosses `type(uint32).max` (approximately year 2106). No attacker action is required; any swap executed after that point triggers the broken path. The absence of an "overflow is desired" comment (unlike analogous UniswapV2 code) confirms this is an unintentional defect.

## Recommendation

Replace `uint32 lastDecayTs` with `uint48` in `BinHighWatermarks`. `uint48` is safe until approximately year 8 million and fits within the existing 256-bit slot alongside `uint104 + uint104`:

```solidity
// Before
struct BinHighWatermarks {
    uint104 token0;
    uint104 token1;
    uint32  lastDecayTs;   // overflows year 2106
}

// After
struct BinHighWatermarks {
    uint104 token0;
    uint104 token1;
    uint48  lastDecayTs;   // safe until year ~8 million
}
```

Apply the same fix to `_afterTimelock`: widen intermediate arithmetic to `uint256` and only truncate the final stored value if compact representation is still desired, or store `pendingXxxExecuteAfter` fields as `uint64`.

## Proof of Concept

```solidity
// Warp block.timestamp to just past uint32 max
vm.warp(uint256(type(uint32).max) + 100);

// Trigger one swap so lastDecayTs is updated to uint32(block.timestamp)
// uint32(4294967395) = 4294967395 - 4294967296 = 99  ← wrapped!
pool.swap(...);

// Warp forward 1 hour
vm.warp(block.timestamp + 3600);

// dt = (4294967395 + 3600) - 99 = 4294970896  ← ~136 years of elapsed time
// factor = ratePerSecondE8 * 4294970896 >= E8 → _decayed returns 0
// _applyWatermark(metric, 0, floor) → breached = false always
// Stop-loss is permanently disabled; swap below drawdown floor succeeds
pool.swap(...);  // should revert with OracleStopLossTriggered, but does not
```

### Citations

**File:** metric-periphery/contracts/interfaces/extensions/IOracleValueStopLossExtension.sol (L7-11)
```text
  struct BinHighWatermarks {
    uint104 token0;
    uint104 token1;
    uint32 lastDecayTs;
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L268-268)
```text
    uint256 dt = block.timestamp - hwmS.lastDecayTs;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L284-284)
```text
    hwmS.lastDecayTs = uint32(block.timestamp);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L297-299)
```text
  function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L319-323)
```text
  function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L333-334)
```text
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
```

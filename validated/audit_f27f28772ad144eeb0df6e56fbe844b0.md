Audit Report

## Title
`drawdownE6 = 0` silently disables all stop-loss protection due to missing lower-bound validation — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

`_validateDrawdown` enforces only an upper bound (`drawdownE6 > E6`), allowing zero to pass without error. When `drawdownE6 = 0` is stored and later read in `_afterSwapOracleStopLoss`, the guard `if (drawdown == 0) return;` causes the function to exit immediately on every swap — skipping all watermark updates and breach detection — while the hook returns the success selector, giving LPs a false sense of protection.

## Finding Description

`_validateDrawdown` at lines 305–307 only rejects values strictly above `1e6`:

```solidity
function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
}
```

Zero passes this check and is stored in `oracleStopLossConfig[pool].drawdownE6`. On every subsequent swap, `_afterSwapOracleStopLoss` reads the value and immediately returns at line 217:

```solidity
uint256 drawdown = cfg.drawdownE6;
if (drawdown == 0) return;   // entire check skipped
```

No bin metrics are computed, no watermarks are ratcheted, and no breach is ever detected. The `afterSwap` hook returns `IMetricOmmExtensions.afterSwap.selector` as if the guard ran normally.

Semantically, `drawdownE6 = 0` means "0% drawdown tolerance" — the strictest possible configuration. The `floorMultiplier` would be `E6 - 0 = E6`, so `_applyWatermark` would flag a breach on any metric decrease (`metric < hwm`). Instead, the early return makes the extension a no-op. By contrast, `decayPerSecondE8 = 0` carries explicit NatSpec: "0 disables decay." No equivalent documentation exists for `drawdownE6`, making the silent bypass a latent misconfiguration trap.

The pool admin can reach this state via the timelocked `proposeOracleStopLossDrawdown` → `executeOracleStopLossDrawdown` path, which also calls `_validateDrawdown` and accepts zero. This constitutes a fund-impacting cap failure: the validation cap should reject zero but does not, allowing the pool admin to (accidentally or otherwise) fully disable the stop-loss while LPs believe it is active.

## Impact Explanation

With `drawdownE6 = 0`, every swap executes regardless of how much value has been extracted from the pool. LPs who deposited under the assumption that the stop-loss extension was actively guarding their positions receive zero protection. An adversary (or a manipulated oracle) can drain LP value across multiple swaps with no circuit-breaker firing. This matches "Broken core pool functionality causing loss of funds" and "direct loss of user principal above Sherlock thresholds."

## Likelihood Explanation

A pool admin intending maximum protection would naturally supply `0` (zero drawdown tolerance). The validation function provides no warning, revert, or documentation to indicate that zero disables the guard rather than enforcing the strictest threshold. The misconfiguration can be introduced at pool creation (factory `initialize`) or later via the propose/execute admin flow. No test in the suite exercises `drawdownE6 = 0` as an active configuration, so the silent bypass has not been caught.

## Recommendation

Reject zero at validation time to close the gap between accepted values and meaningful configurations:

```diff
function _validateDrawdown(uint256 drawdownE6) private pure {
+   if (drawdownE6 == 0) revert OracleStopLossDrawdownTooLarge(drawdownE6);
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
}
```

Alternatively, remove the early return and let `floorMultiplier = E6` enforce the zero-tolerance semantics correctly, accompanied by a NatSpec clarification analogous to the one on `decayPerSecondE8`.

## Proof of Concept

```solidity
// Pool initialized with drawdownE6 = 0 — accepted by _validateDrawdown
extension.initialize(
    address(pool),
    abi.encode(uint32(0), uint32(58), uint32(0))  // drawdown=0, decay=58, timelock=0
);

// Bin value drops 50% — stop-loss should fire
_storeBin(0, 500, 500, BIN_SHARES);

// afterSwap returns success selector; no revert despite 50% value loss
extension.afterSwap(
    address(0), address(0), true, 0, 0,
    _packSlot0(0), _packSlot0(0),
    uint128(Q64), uint128(Q64),
    0, 0, 0, ""
);
// Expected: OracleStopLossTriggered revert
// Actual:   silent success — stop-loss completely bypassed
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L56-62)
```text
    (uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
    _validateDrawdown(drawdownE6);
    _validateDecay(decayPerSecondE8);

    oracleStopLossConfig[pool] = PoolStopLossConfig({
      drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
    });
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L215-217)
```text
    PoolStopLossConfig memory cfg = oracleStopLossConfig[pool_];
    uint256 drawdown = cfg.drawdownE6;
    if (drawdown == 0) return;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-307)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
  }
```

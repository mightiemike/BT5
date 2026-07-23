### Title
`OracleValueStopLossExtension` silently disables all stop-loss protection when `drawdownE6 = 0` is configured — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss()` contains an early-return guard `if (drawdown == 0) return;` that completely skips watermark evaluation and breach detection whenever `drawdownE6` is configured as zero. Because `_validateDrawdown` accepts zero as a valid value (it only rejects values strictly greater than `1e6`), a pool initialized or updated with `drawdownE6 = 0` silently loses all stop-loss protection on every subsequent swap, while LPs believe the extension is actively guarding their positions.

---

### Finding Description

`_validateDrawdown` enforces only an upper bound:

```solidity
function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
}
```

Zero passes this check without error. The value is stored and later read in `_afterSwapOracleStopLoss`:

```solidity
uint256 drawdown = cfg.drawdownE6;
if (drawdown == 0) return;          // ← entire check skipped
```

When `drawdown == 0`, the function returns immediately. No bin metrics are computed, no watermarks are updated, and no breach is ever detected. The `afterSwap` hook returns `IMetricOmmExtensions.afterSwap.selector` as if everything is fine.

The analog to the external report is exact:

| External (LendingTerm) | Metric OMM (OracleValueStopLossExtension) |
|---|---|
| `interestRate = 0` is a valid loan config | `drawdownE6 = 0` is a valid stop-loss config |
| Derived `interestRepaid = 0` | Derived `drawdown = 0` |
| `require(interestRepaid != 0)` blocks a valid repay | `if (drawdown == 0) return` bypasses a valid guard |
| DoS on partial repay | Silent disabling of stop-loss protection |

The zero-drawdown case is semantically the **strictest** possible configuration (0% value drop tolerated), yet the code treats it as "disabled." The `decayPerSecondE8` parameter has an explicit NatSpec comment "0 disables decay," but `drawdownE6` has no such documentation, making the silent bypass a latent misconfiguration trap.

---

### Impact Explanation

When `drawdownE6 = 0`:

1. `_afterSwapOracleStopLoss` returns at line 217 without touching any watermark or running any breach check.
2. Every swap executes regardless of how much value has been extracted from the pool.
3. LPs who deposited into a pool believing the stop-loss extension was actively protecting them receive zero protection.
4. An adversary (or a manipulated oracle) can drain LP value across multiple swaps with no circuit-breaker firing.

This is a direct loss of LP principal caused by a broken core protection hook — matching the "Broken core pool functionality causing loss of funds" impact gate.

---

### Likelihood Explanation

- `drawdownE6 = 0` is accepted by `_validateDrawdown` with no warning, revert, or documentation.
- A pool admin intending "maximum protection" (zero drawdown tolerance) would naturally supply `0`.
- The factory `initialize` path also accepts `drawdownE6 = 0` at pool creation time, so the misconfiguration can be baked in from deployment.
- No test in the suite exercises `drawdownE6 = 0` as an active (non-reinitialization) configuration, so the silent bypass has never been caught.

---

### Recommendation

**Option A — Reject zero at validation time** (preferred, mirrors the external fix of removing the bad check):

```diff
function _validateDrawdown(uint256 drawdownE6) private pure {
+   if (drawdownE6 == 0) revert OracleStopLossDrawdownTooLarge(drawdownE6);
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
}
```

**Option B — Guard the transfer, not the whole function** (analogous to the suggested fix in the external report — add a conditional instead of an early return):

```diff
- if (drawdown == 0) return;
+ // drawdown == 0 means 0% tolerance: any metric drop is a breach; proceed normally.
```

And add a zero-drawdown test confirming that a metric drop of any size triggers `OracleStopLossTriggered`.

---

### Proof of Concept

```solidity
// Pool initialized with drawdownE6 = 0 (accepted by _validateDrawdown)
extension.initialize(
    address(pool),
    abi.encode(uint32(0), uint32(58), uint32(0))  // drawdown=0, decay=58, timelock=0
);

// First swap: watermarks should be set, but _afterSwapOracleStopLoss returns immediately.
// Bin value drops 50% — stop-loss should fire, but does not.
_storeBin(0, 500, 500, BIN_SHARES);   // half the original value

// This call should revert with OracleStopLossTriggered but instead succeeds silently.
extension.afterSwap(
    address(0), address(0), true, 0, 0,
    _packSlot0(0), _packSlot0(0),
    uint128(Q64), uint128(Q64),
    0, 0, 0, ""
);
// No revert — stop-loss completely bypassed despite 50% value loss.
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

### Title
`METRIC_SCALE = 1e6` Too Small Causes Per-Share Stop-Loss Metric to Round Permanently to Zero, Bypassing the Oracle Value Guard — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension` uses `METRIC_SCALE = 1e6` as its fixed-point precision factor when computing per-share bin value metrics. For any pool whose `initialScaledToken0PerShareE18 < 1e12` (achievable with standard 18-decimal tokens and a small but factory-valid `initialAmount0PerShareE18`), the per-share metric permanently rounds to zero on every swap. Because the watermark is ratcheted from the live metric, it is also permanently zero. `_applyWatermark(0, 0, …)` always returns `(0, false)`, so the stop-loss guard never fires regardless of how much LP value is extracted.

---

### Finding Description

`_metrics` computes the per-share value of each bin:

```solidity
uint256 private constant METRIC_SCALE = 1e6;

function _metrics(uint104 t0, uint104 t1, uint256 totalShares, uint256 minShares, uint256 midPriceX64)
    private pure returns (uint256 metricT0, uint256 metricT1)
{
    uint256 shares = totalShares < minShares ? minShares : totalShares;
    uint256 t0ps = Math.mulDiv(uint256(t0), METRIC_SCALE, shares);   // ← rounds to 0 when t0*1e6 < shares
    uint256 t1ps = Math.mulDiv(uint256(t1), METRIC_SCALE, shares);
    metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64
### Title
`SwapAllowlistExtension` Checks Router Address Instead of Ultimate Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg
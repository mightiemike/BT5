### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument the pool passes, which is always `msg.sender` of the `pool.swap
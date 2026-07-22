### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any user to bypass the per-pool swap allowlist via the router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` calls `pool.swap()`, that `sender` is the router's address, not the end user's. A pool admin who allowlists the router to restore router usability for their allowlisted users inadvertently opens the pool to every user who routes
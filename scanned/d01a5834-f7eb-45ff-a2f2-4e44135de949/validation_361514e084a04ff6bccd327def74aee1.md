Looking at the extension guard and hook plumbing in the Metric OMM codebase, I need to trace how the `SwapAllowlistExtension` and `DepositAllowlistExtension` receive and check actor identity when calls are routed through `MetricOmmSimpleRouter` or `MetricOmmPoolLiquidityAdder`.

### Title
`SwapAllowlistExtension` gates on the router's address instead of the end-user's identity, allowing any user to bypass a per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery
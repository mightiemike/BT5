### Title
`DepositAllowlistExtension` gates position `owner` instead of actual depositor `sender`, allowing any unprivileged user to bypass the deposit allowlist via `MetricOmmPoolLiquidityAdder` — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` checks `allowedDepositor[pool][owner]` (the position owner) rather than the actual caller (`sender`). Because `MetricOmmPoolLiquidityAdder` accepts a caller-supplied `owner` parameter and passes it directly to the pool, any unprivile
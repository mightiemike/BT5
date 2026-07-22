Looking at the extension guard plumbing, I need to trace the actor identity through the swap allowlist path when the standard periphery router is used.

**Key chain to trace:**

1. `MetricOmmSimpleRouter.exactInputSingle` → calls `pool.swap(params.recipient, ...)` with `msg.sender = router`
2. `MetricOmmPool.swap` → calls `_beforeSwap(msg.sender, recipient, ...)` — passes the **router** as `sender`
3. `SwapAllowlistExtension.beforeSwap(sender, ...)` → checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool, `sender` = **router**

Compare with the deposit path:

1. `MetricOmmPoolLiquidityAdder._addLiquidity` → calls `pool.addLiquidity(positionOwner, ...)`
2. `MetricOmmPool.addLiquidity` → calls `_beforeAddLiquidity(msg.sender, owner, ...)` — passes the **actual user** as `owner`
3. `DepositAllowlistExtension.beforeAddLiquidity(_, owner, ...)` → checks `allowedDepositor[msg.sender][owner]` — correctly gates the **actual user**

The asymmetry is the root cause. `DepositAllowlistExtension` checks `owner` (actual user), while `SwapAllowlistExtension` checks `sender` (the router
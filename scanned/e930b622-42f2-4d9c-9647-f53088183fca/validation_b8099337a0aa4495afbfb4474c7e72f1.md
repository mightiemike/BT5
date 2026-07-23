Looking at the `SwapAllowlistExtension.beforeSwap` actor binding and how the router calls the pool, I need to trace the exact `sender` value that reaches the allowlist check.

From `MetricOmmSimpleRouter.exactInputSingle` (line 72-80), the router calls `pool.swap(params.recipient, ...)` — so `msg.sender` to the pool is the **router address**.

From `MetricOmmPool.simulateSwapAndRevert` (line 321-331), the pool calls `_beforeSwap(msg.sender, recipient, ...)` — confirming the pool passes its own `msg.sender` (the router) as `sender`.

In `ExtensionCalling._beforeSwap` (line 160-176), that `sender` value is forwarded verbatim to the extension.

In `SwapAllowlistExtension.beforeSwap` (line
### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates pool swaps by checking the `sender` argument passed from the pool. Because `MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as `sender`, any swap routed through `MetricOmmSimpleRouter` presents the router's address to the extension, not the end user's address. A pool admin who allowlists the router to enable router-mediated swaps for their approved users inadvertently opens the pool to every user on the router, defeating the per-user allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAll
### Title
`SwapAllowlistExtension` Allowlist Bypassed via Router When Router Is Allowlisted — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals the `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the `msg.sender` of `pool.swap()`, so the extension checks whether the **router** is allowlisted, not the actual end user. A pool admin who allowlists the router — the natural step to let allowlisted users reach the pool through the router — inadvertently opens the gate to every user on the network.

### Finding Description
`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwap
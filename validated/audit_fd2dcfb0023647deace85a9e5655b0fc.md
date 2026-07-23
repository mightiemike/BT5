### Title
`SwapAllowlistExtension` checks the router address instead of the ultimate user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct `msg.sender` of `pool.swap()`. When users enter through `MetricOmmSimpleRouter`, `sender` is the router contract, not the end user. If the pool admin allowlists the router to enable router-mediated swaps for permitted users, every unpermitted user can bypass the allowlist by routing through the same public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/Swap
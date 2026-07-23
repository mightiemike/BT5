### Title
`SwapAllowlistExtension` gates on router address instead of end-user, allowing any user to bypass the swap allowlist via the router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. If the pool admin allowlists the router — a natural configuration to let allowlisted users access the router — every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as `sender` to `_beforeSwap`:

<cite repo="Oyahkilomeikhide/2026-07-metric-dev-oyakhil-main--018" path="metric-
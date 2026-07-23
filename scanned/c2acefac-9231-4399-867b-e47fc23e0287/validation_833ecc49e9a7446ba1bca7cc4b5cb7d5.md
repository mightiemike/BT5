### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any caller to bypass the per-user swap allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` — the direct `msg.sender` of `pool.swap()` — against the per-pool allowlist. When swaps are routed through the public `MetricOmmSimpleRouter`, `sender` is the router contract, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged caller can bypass the per-user gate by
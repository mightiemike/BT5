### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, enabling full allowlist bypass via MetricOmmSimpleRouter when the router is allowlisted — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router address — a natural step to let allowlisted users access the router — every unprivileged caller can bypass the allowlist by routing through the router.

---

###
### Title
SwapAllowlistExtension gates the router address instead of the actual user, enabling any user to bypass per-user swap restrictions via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary
`SwapAllowlistExtension.beforeSwap` checks `sender`, which is the direct caller of the pool's `swap` function (`msg.sender` of the pool call). When swaps are routed through `MetricOmmSimpleRouter`, `sender` equals the router address, not the actual end-user. If the router is allowlisted, any user can bypass the per-user swap restriction by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg
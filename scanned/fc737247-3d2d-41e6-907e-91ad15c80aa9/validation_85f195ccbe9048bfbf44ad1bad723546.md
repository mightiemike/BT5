### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the direct `msg.sender` of `MetricOmmPool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, that value is the router's address, not the end-user's address. A pool admin who allowlists the router to enable router-based swaps inadvertently grants swap access to every user of the router, bypassing the per-user allowlist entirely.

---

### Finding Description

**Analog classification.** The external bug is a *wrong-state-read after a state-changing action*: `check_long_margin_call` mutates the tree, then `is_long_margin_call` re-reads the now-changed tree and returns the wrong boolean, causing the downstream `margin_call` flag to be set incorrectly. The Metric OMM analog is a *wrong-actor read*: `beforeSwap` reads `sender` (the direct pool caller) as the identity to gate, but after the swap is routed through the periphery the value of `sender` is the router, not the user the admin intended to gate. In both cases a guard reads a value that no longer represents the entity the protocol intended to check, and the downstream decision (allow/block) is wrong.

**Root cause in `SwapAllowlistExtension`.** In `MetricOmmPool.swap()` the pool dispatches the before-swap hook as:

```solidity
_beforeSwap(
    msg.sender,   // ← sender = direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to every configured extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`. When the user goes through `MetricOmmSimpleRouter`, the router calls `pool.swap()`, so `sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Bypass path.** A pool admin who wants to enable router-based swaps must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the check passes for *every* caller of the router, including addresses the admin never intended to permit. There is no field in the `beforeSwap` signature that carries the original end-user's address, and the `
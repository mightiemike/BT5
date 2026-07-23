Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter against a per-pool allowlist, but `MetricOmmPool.swap` passes `msg.sender` as `sender`, which is the router contract address when a user routes through `MetricOmmSimpleRouter`. The allowlist therefore checks whether the router is permitted, not whether the actual user is permitted. Any user can bypass the per-user allowlist by routing through the official periphery router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces access control at `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol` L37:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool (correct for namespacing) and `sender` is the first argument passed by the pool.

`MetricOmmPool.swap` at `metric-core/contracts/MetricOmmPool.sol` L230-231 passes `msg.sender` as the `sender` argument to `_beforeSwap`:
```solidity
_beforeSwap(
    msg.sender,  // ← this is the router when called via router
```

`ExtensionCalling._beforeSwap` at `metric-core/contracts/ExtensionCalling.sol` L160-176 forwards this value unchanged as the `sender` argument to the extension.

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly at `metric-periphery/contracts/MetricOmmSimpleRouter.sol` L72-80. The pool's `msg.sender` is the router, so `sender` delivered to the extension is the router's address. The allowlist check becomes `allowedSwapper[pool][router]` — it checks whether the router contract is permitted, not whether the actual end-user is permitted.

The same wrong-actor binding applies to `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` multi-hop paths (L165-181), all of which call `pool.swap` with the router as `msg.sender`.

Existing guards are insufficient: there is no mechanism in the pool, extension calling path, or extension itself to recover the originating user address from the router call.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses. The invariant is: only allowlisted addresses may swap. This invariant is broken when users route through `MetricOmmSimpleRouter`:

- **Scenario A — Router allowlisted**: The admin allowlists the router address so that router-based swaps work. Because the check is on the router address, every user (including non-allowlisted ones) can now swap by going through the router. The per-user allowlist is completely bypassed. Unauthorized users trade on a pool that should be restricted, violating the curation policy and potentially draining LP assets at prices the pool admin intended to offer only to specific counterparties.
- **Scenario B — Router not allowlisted**: Even individually allowlisted users cannot swap through the router, breaking the expected UX of the supported periphery path.

Scenario A is the fund-impacting case and constitutes an admin-boundary break: pool admin's allowlist configuration is bypassed by an unprivileged path.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the documented, supported periphery entry point for swaps. Pool admins who configure `SwapAllowlistExtension` and also want router-based swaps to work will naturally allowlist the router, triggering the bypass. The exploit requires no special privileges — any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` with a curated pool address. The precondition (router allowlisted) is the natural and expected configuration for any pool that supports router-based swaps.

## Recommendation
`SwapAllowlistExtension.beforeSwap` should check the actual user rather than the immediate caller of the pool. Preferred approaches:

1. **Dedicated router forwarding**: The router encodes `msg.sender` (the originating user) into `extensionData`; the extension decodes and checks it against the allowlist. This requires a trusted encoding convention between the router and extension.
2. **Pool-level `swapFrom` entry point**: The pool exposes a `swapFrom(address user, ...)` entry point that the router calls, so `sender` is always the real user rather than the router.
3. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is the user, but this breaks for multi-hop where intermediate recipients are the router itself.

## Proof of Concept
```solidity
// Pool admin sets up a curated pool with SwapAllowlistExtension.
// Admin allowlists the router so router-based swaps work.
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Alice is NOT individually allowlisted.
// Alice calls the router — the extension sees sender = router (allowlisted) → passes.
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: alice,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    extensionData: "",
    deadline: block.timestamp
}));
// Alice's swap succeeds despite not being on the per-user allowlist.
// The check allowedSwapper[pool][router] == true passes for every caller.
```
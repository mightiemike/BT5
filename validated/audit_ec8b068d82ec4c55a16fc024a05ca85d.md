Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end-user, enabling full per-user allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is intended to gate pool swaps by individual swapper address per pool. However, `MetricOmmPool.swap` passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`, which forwards it unchanged to the extension. The extension then evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. Any user routing through `MetricOmmSimpleRouter` either bypasses the allowlist entirely (if the router is allowlisted) or is permanently blocked from swapping through the router (if only individual users are allowlisted).

## Finding Description
`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // whoever called pool.swap() — the router when routed
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension hook via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`.

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the direct caller of `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
  );
```

So `sender` = router address, and the check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The NatSpec states the contract "Gates `swap` by swapper address, per pool" but the implementation gates by the direct pool caller, not the end user.

**Mode A — Full bypass:** Admin allowlists the router so normal users can swap. Every address — including those the admin never allowlisted — passes the guard because `allowedSwapper[pool][router] == true`.

**Mode B — Permanent DoS:** Admin allowlists individual user addresses (not the router). Those users cannot swap through the router because `allowedSwapper[pool][router] == false`. The router is the primary user-facing interface, making the pool's swap functionality unusable for all allowlisted users.

## Impact Explanation
Mode A constitutes broken core pool functionality and a direct admin-boundary break: the pool admin's per-user access control invariant is violated by any unprivileged trader routing through the standard interface. For permissioned AMMs (KYC'd counterparties, restricted LP pools), unauthorized addresses execute swaps against pool liquidity, directly exposing LPs to trades with unintended counterparties. Mode B renders the swap flow unusable for all allowlisted users through the router, constituting broken core swap functionality.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard user-facing swap interface. Any pool deploying `SwapAllowlistExtension` expecting per-user access control is affected the moment any user routes through the router — no special privilege or unusual setup is required. The trigger is the ordinary swap path. The `onlyPoolAdmin` guard on `setAllowedToSwap` does not mitigate this; the admin correctly configures user addresses, but the hook reads the wrong address at runtime.

## Recommendation
The pool must forward the true end-user identity to the extension. The preferred approach is to have the router encode `msg.sender` (the actual user) into `extensionData`, and have the extension decode and verify it. Alternatively, the extension can check `recipient` as a proxy for the intended swapper, or the pool interface can be extended with a dedicated `swapper` field distinct from `sender`. As a short-term mitigation, document that the extension only gates by direct caller (router/contract), not end user, and rename the mapping and NatSpec accordingly.

## Proof of Concept
```
Setup:
  - Pool deployed with SwapAllowlistExtension in beforeSwap slot
  - Admin calls setAllowedToSwap(pool, router, true)   // allowlists the router
  - Admin never calls setAllowedToSwap(pool, alice, true)  // alice is NOT allowlisted

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=alice, ...)  →  pool's msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] == true  →  PASSES
  5. Alice's swap executes successfully despite never being allowlisted

Foundry test outline:
  - Deploy pool with SwapAllowlistExtension
  - setAllowedToSwap(pool, address(router), true)
  - vm.prank(alice); router.exactInputSingle(...)
  - Assert swap succeeds (alice bypasses allowlist)
  - Assert isAllowedToSwap(pool, alice) == false (alice was never allowlisted)
```
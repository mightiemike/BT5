Audit Report

## Title
`SwapAllowlistExtension.beforeSwap()` checks the immediate pool caller instead of the originating user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users simultaneously opens the pool to every user, completely defeating the per-user access control.

## Finding Description

**Root cause — `MetricOmmPool.swap()` passes `msg.sender` (the immediate caller) as `sender` to extensions:**

In `MetricOmmPool.sol` lines 230–240, `_beforeSwap` is called with `msg.sender` as the first argument. When the call originates from `MetricOmmSimpleRouter`, `msg.sender` is the router contract address, not the end user.

**`SwapAllowlistExtension.beforeSwap()` checks that value directly:**

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()` — the router.

**`MetricOmmSimpleRouter.exactInputSingle()` never forwards the originating user to the pool:**

```solidity
// MetricOmmSimpleRouter.sol L71–80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

`msg.sender` (the originating user) is stored only in transient storage for the payment callback. It is never passed to `pool.swap()` and is therefore invisible to the extension. The extension receives `sender = router address`.

**Exploit path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` in `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, router, true)` to let allowlisted users use the router.
3. Non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` = `true` → passes.
6. Swap executes. Attacker swaps against the curated pool without ever being individually allowlisted.

The same structural issue applies to `exactInput` (multi-hop, lines 99–125) and `exactOutputSingle`/`exactOutput` paths in `MetricOmmSimpleRouter.sol`.

**Existing guards are insufficient:** The extension has no mechanism to distinguish a direct pool caller from a router acting on behalf of an end user. The `extensionData` field is passed through but the extension does not decode it, and the router passes `""` as `extensionData` anyway (line 79).

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against the pool's liquidity at oracle-quoted prices without the pool admin's consent. This constitutes broken core pool functionality (the allowlist invariant) and a direct loss of LP principal, as LP assets are traded against by unauthorized parties at oracle-derived bid/ask prices.

## Likelihood Explanation

The scenario requires the pool admin to allowlist the router address. This is a natural and expected action: any pool admin who wants their allowlisted users to use the standard router must add the router to the allowlist. The admin is likely unaware that doing so simultaneously opens the pool to all users, because the extension's parameter name (`sender`) and NatSpec ("Gates `swap` by swapper address") imply it represents the originating swapper, not an intermediate contract. The router is a public, permissionless contract, so once allowlisted, exploitation requires no special access — any EOA can call `exactInputSingle`.

## Recommendation

The extension must gate on the economically relevant actor — the originating user — not the immediate pool caller. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires a coordinated convention between the router and the extension.
2. **Add an `originator` field to the swap interface**: Pass a dedicated originator address alongside `sender` so extensions can distinguish the two actors.

Until resolved, pool admins using `SwapAllowlistExtension` must not allowlist the router address, which means their allowlisted users cannot use the router — a significant usability constraint.

## Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension in beforeSwap hook
// 2. Pool admin allowlists the router:
swapAllowlistExtension.setAllowedToSwap(pool, address(router), true);

// 3. Non-allowlisted attacker calls the router:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// pool.swap() is called with msg.sender = router
// extension checks allowedSwapper[pool][router] = true → passes
// attacker successfully swaps against the curated pool
```
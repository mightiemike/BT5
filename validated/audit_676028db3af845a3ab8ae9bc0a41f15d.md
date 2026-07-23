Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End-User Identity, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. A pool admin who allowlists the router address to enable router-based swaps inadvertently grants every user — including those not individually allowlisted — the ability to bypass the per-user gate.

## Finding Description
**Root cause — wrong identity checked:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool via `ExtensionCalling._callExtensionsInOrder`). `sender` is whatever the pool received as `msg.sender` of `pool.swap()`.

**Pool passes its own `msg.sender` as `sender`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    ...
```

**Router calls `pool.swap()` without forwarding user identity:**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The router never encodes the originating user's address into `pool.swap()`. The pool therefore sees `msg.sender = router`, and the extension evaluates `allowedSwapper[pool][router]` — a single boolean covering every user of the router — rather than `allowedSwapper[pool][user]`. The same collapse occurs in `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181).

**Existing guards are insufficient:** There is no mechanism in `extensionData` or the hook signature to carry the originating user's address from the router to the extension. The `isAllowedToSwap` view function returns `true` for the router without revealing that this grants access to all users.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router address (a natural step to enable router-based swaps for approved users) inadvertently opens the pool to every user. Any address can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and execute swaps on the curated pool, bypassing the per-user allowlist entirely. This breaks the pool's access-control invariant and allows unauthorized parties to trade against LP assets in a pool explicitly configured to restrict access. The inverse is equally broken: if the admin does not allowlist the router, individually allowlisted users cannot use the router at all, losing slippage and deadline protection. This constitutes broken core pool functionality causing potential loss of funds (unauthorized trades against LP assets) and unusable swap flows (allowlisted users forced off the router).

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported periphery path for end-user swaps. A pool admin who wants to run a curated pool and still support the standard router will naturally allowlist the router address. The system provides no documentation warning against this, and the `isAllowedToSwap` view function returns `true` for the router without revealing that this grants access to all users. The bypass requires no special privileges — any EOA can call the router. The mistake is easy to make and the exploit is trivially repeatable.

## Recommendation
The `SwapAllowlistExtension` must gate on the economic actor (the end user), not the call-chain intermediary (the router). Two complementary fixes:

1. **Extension side:** Add a dedicated `swapper` field to `extensionData` that the router populates with `msg.sender`, and have the extension verify and consume it. Alternatively, redesign the hook signature to carry the originating user explicitly.

2. **Router side:** The router should encode `msg.sender` into `extensionData` in a standard, verifiable way so that allowlist extensions can authenticate the true initiator regardless of call depth.

Until resolved, pool admins must not allowlist the router address on curated pools; they must require users to call `pool.swap()` directly, forfeiting router safety features.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to enable router-based swaps for approved users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker (not individually allowlisted) calls:
      router.exactInputSingle(ExactInputSingleParams{
          pool:      curated_pool,
          recipient: attacker,
          ...
      })

Trace:
  router.exactInputSingle()
    → pool.swap(recipient=attacker, ...)   [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, attacker receives output tokens

Result:
  attacker successfully swaps on a pool configured to block them.
  The per-user allowlist is completely bypassed.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension` as `beforeSwap` hook, allowlist only the router, call `router.exactInputSingle` from an address not individually allowlisted, and assert the swap succeeds (no `NotAllowedToSwap` revert).
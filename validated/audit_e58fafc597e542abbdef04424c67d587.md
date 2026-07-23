Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` as the direct caller of `MetricOmmPool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating user. A pool admin who allowlists the router to permit their curated users to trade through the supported periphery simultaneously grants unrestricted swap access to every address on the network, because any caller can reach the pool through the same public router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` encodes that value verbatim and calls each configured extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
  )
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is on the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router the `msg.sender` of that call:

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

The extension therefore receives `sender = address(router)` and evaluates `allowedSwapper[pool][router]`. The originating user's address is never seen by the guard. The router stores `msg.sender` only in transient callback context for payment purposes (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`), but this value is never forwarded to the pool or extension.

## Impact Explanation

The `SwapAllowlistExtension` is a core access-control hook for curated pools. When a pool admin allowlists the router (the necessary operational step to let their approved users trade through the supported periphery), the guard passes for every call arriving through the router regardless of who the originating user is. The entire curation policy is nullified: users who were explicitly blocked can trade freely through the public, permissionless router. This constitutes broken core pool functionality — the extension's swap access control is completely ineffective in the primary supported swap path.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported swap interface for end users. Any pool admin who configures a `SwapAllowlistExtension` and also wants their allowlisted users to use the router will trigger this condition. The router is a public, permissionless contract requiring no privileged access. The attacker only needs to call a standard router function targeting the curated pool address.

## Recommendation

Pass the originating user through the call chain so the extension can gate on the economically relevant actor. The simplest correct fix is to have the router store `msg.sender` in a dedicated transient storage slot before calling the pool, and expose a `getSwapOriginator()` view that the extension can call back to retrieve the true user. Alternatively, add an `originator` field to the swap call or extension data and have the pool forward it as a separate argument to the extension. Documentation alone is insufficient because the operational requirement (allowlisting the router) and the security requirement (blocking non-allowlisted users) are mutually exclusive under the current design.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended user
  - Pool admin calls setAllowedToSwap(pool, router, true)      // needed so alice can use the router

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({
        pool: curated_pool,
        tokenIn: token0,
        recipient: bob,
        amountIn: X,
        ...
    })
  - Router calls pool.swap(bob_recipient, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes; bob receives output tokens from the curated pool
  - alice's exclusive access policy is violated with zero privileged setup
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist `alice` and `router`, then call `router.exactInputSingle` from `bob` and assert the swap succeeds (no `NotAllowedToSwap` revert).
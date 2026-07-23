Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing any user to bypass the per-pool swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router address, not the end user. If the router is allowlisted (required for any router-mediated swap to succeed), every non-allowlisted user can bypass the curated pool's swap gate by simply calling the router. The pool admin faces an impossible choice: allowlist the router (breaking the allowlist for all users) or don't (making the router unusable for legitimate users).

## Finding Description

`SwapAllowlistExtension.beforeSwap` at L37 performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the address passed from `MetricOmmPool.swap()`. At L230–231 of `MetricOmmPool.sol`, the pool passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    ...
);
```

In `MetricOmmSimpleRouter.exactInputSingle()` at L71–80, the router stores the original user only in transient storage for the payment callback and calls `pool.swap()` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

The pool therefore sees `msg.sender = router`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The same substitution occurs for multi-hop `exactInput` (all hops after the first use `address(this)` as payer, L103) and `exactOutputSingle`/`exactOutput`.

**Exploit path:**
1. Pool admin deploys pool with `SwapAllowlistExtension`, allowlists `alice` and the router (required for alice to use the standard interface).
2. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
3. Router calls `pool.swap(recipient, ...)` — `msg.sender` to pool = router.
4. Pool calls `extension.beforeSwap(router, ...)` — `msg.sender` to extension = pool.
5. Extension checks: `allowedSwapper[pool][router] == true` → passes.
6. Bob's swap executes, bypassing the allowlist entirely.

No existing guard prevents this. The extension has no mechanism to distinguish the router acting on behalf of an allowlisted user from the router acting on behalf of an arbitrary user.

## Impact Explanation

Unauthorized users can trade against LP positions in a pool designed to restrict counterparties, directly exposing LP principal to unintended swap flows. The allowlist invariant — that only approved addresses may swap — is completely broken for any pool that allowlists the router. This constitutes a broken core pool functionality (the allowlist extension's sole purpose) causing fund-impacting exposure of LP assets to unintended counterparties, matching the "Admin-boundary break" and "Broken core pool functionality causing loss of funds" impact categories.

## Likelihood Explanation

Any pool deploying `SwapAllowlistExtension` and expecting users to interact via `MetricOmmSimpleRouter` is affected. The router is the primary supported periphery path. A pool admin who allowlists the router to enable normal UX inadvertently opens the gate to all users. The attacker requires no special privileges — a single `exactInputSingle` call suffices. The condition (router allowlisted) is a necessary operational requirement, making this reliably triggerable.

## Recommendation

The extension must gate on the economically relevant actor (the end user), not the immediate caller of `pool.swap()`. Concrete options:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension verifies it (requires a trust assumption on the router or a signature scheme).
2. **Revert if `sender` is a known router**: The extension detects router-mediated calls and reverts, documenting incompatibility with the router.
3. **Separate allowlist for routers with user-forwarding**: Require routers to forward the original caller's address through a standardized interface that the extension can verify.

## Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true).
3. Pool admin calls setAllowedToSwap(pool, router, true).
   (required so alice can use the standard router interface)
4. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, ...) — msg.sender to pool = router.
6. Pool calls extension.beforeSwap(router, ...) — msg.sender to extension = pool.
7. Extension checks: allowedSwapper[pool][router] == true → passes.
8. bob's swap executes successfully, bypassing the allowlist.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

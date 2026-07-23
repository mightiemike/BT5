Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any caller to bypass per-pool swap allowlists via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, so when `MetricOmmSimpleRouter` calls `pool.swap(...)`, the extension receives `sender = address(router)`. `SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. If the pool admin allowlists the router (the only way to permit router-mediated swaps for any allowlisted user), every non-allowlisted address can bypass the restriction by routing through `MetricOmmSimpleRouter`.

## Finding Description

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` parameter to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(params.recipient, ...)`, the pool's `msg.sender` is the router contract: [4](#0-3) 

The check therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The actual user's address is only available as `recipient` (the output-token destination), which the extension ignores entirely. There is no mechanism in the current hook signature or `extensionData` path that propagates the originating EOA from the router to the extension.

## Impact Explanation

Two mutually exclusive failure modes exist. If the router is **not** allowlisted, every allowlisted user is silently blocked from using the router; they must call the pool directly, breaking expected UX. If the router **is** allowlisted (the natural operational step when the admin wants to support router-mediated swaps for their allowlisted users), every non-allowlisted address can bypass the restriction by calling `router.exactInputSingle`. A pool intended to be restricted to KYC'd or institutional counterparties becomes open to any EOA. If the pool holds concentrated liquidity at oracle-anchored prices, an unrestricted attacker can drain favorable bins before the oracle updates, causing direct loss of LP principal — a High-severity impact under the contest gate (broken core pool functionality / direct loss of LP assets).

## Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless periphery contract deployed alongside the protocol. No special role or privileged setup is required; any EOA can call `exactInputSingle`. The only precondition is that the pool admin has allowlisted the router, which is the natural and expected operational step when the admin wants allowlisted users to be able to use the router. The bypass is one hop away from any user who knows the router address.

## Recommendation

Pass the originating user's address through `extensionData` and have `SwapAllowlistExtension` decode and verify it, or add a dedicated `swapper` field to the `beforeSwap` hook signature that the pool populates from a trusted transient-storage context set by the router before calling `pool.swap`. As a short-term mitigation, document explicitly that allowlisting the router is equivalent to `allowAll = true` and block the router address from being added to `allowedSwapper` at the extension level, forcing admins to use `setAllowAllSwappers` explicitly if that is the intent.

## Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension; configure beforeSwap order = extension 1.
  2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  3. Admin calls setAllowedToSwap(pool, router, true)  // admin adds router so alice can use it

Attack:
  4. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, recipient: bob, ...})
     → router calls pool.swap(recipient=bob, ...)          [MetricOmmSimpleRouter.sol L72-80]
     → pool calls _beforeSwap(sender=router, ...)          [MetricOmmPool.sol L230-240]
     → extension checks allowedSwapper[pool][router] == true  ✓  [SwapAllowlistExtension.sol L37-39]
     → swap proceeds; bob receives output tokens

Result:
  Bob, who is not in the allowlist, successfully swaps on a pool
  intended to be restricted to alice only. The allowlist invariant
  is broken; LP assets are exposed to any unpermissioned address.
```

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

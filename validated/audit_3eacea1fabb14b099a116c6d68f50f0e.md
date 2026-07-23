Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which resolves to `msg.sender` of the pool's `swap` call — the direct caller, not the economic initiator. When `MetricOmmSimpleRouter` is used, `sender` is always the router's address. Because the admin must allowlist the router for any allowlisted user to use the periphery, once that step is taken every unprivileged caller can bypass the per-user gate by routing through the public router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller); `sender` is the first argument forwarded by the pool. [1](#0-0) 

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` — whoever called `pool.swap` — as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` is used, the router is the direct caller of `pool.swap`, so `sender = router address`: [4](#0-3) 

The allowlist lookup therefore becomes `allowedSwapper[pool][router]`. For any allowlisted user to use the router, the admin must add the router to the allowlist. Once the router is allowlisted, the check passes for **every** caller, because the pool always sees `sender = router` regardless of who initiated the transaction. The same pattern applies to `exactInput` (multi-hop) and `exactOutputSingle` / `exactOutput`. [5](#0-4) 

## Impact Explanation
Any unprivileged user can bypass the swap allowlist on a restricted pool by routing through `MetricOmmSimpleRouter`. The pool admin's intent to restrict swaps to vetted counterparties is nullified. Unauthorized swappers can trade against the pool's LP positions, extracting value from LPs who deposited under the assumption that only allowlisted parties would trade. This constitutes a broken core pool functionality / admin-boundary break with direct LP-asset exposure, meeting the contest's High severity threshold.

## Likelihood Explanation
The admin **must** allowlist the router for allowlisted users to use the periphery — this is a natural and expected operational step. Once done, the bypass is trivially reachable by any user who calls the public router with no special privileges or setup. No front-running, flash loans, or special conditions are required; a single direct call to `router.exactInputSingle` suffices.

## Recommendation
Pass the end-user's address through the swap path so the allowlist gates the correct identity. The cleanest fix is to add an explicit `swapper` field to the router's `ExactInputSingleParams` (defaulting to `msg.sender`) and forward it as the `sender` argument to `pool.swap`. Alternatively, the pool could accept an explicit `originator` parameter that the router populates from its stored payer context (`_getPayer()` is already tracked in transient storage), but this couples the core to periphery conventions. Either way, the extension must receive the economic initiator, not the intermediate contract.

## Proof of Concept
1. Deploy pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, userA, true)` — allowlists user A.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so user A can use the router.
4. Non-allowlisted user B calls `router.exactInputSingle({pool: pool, recipient: B, ...})`.
5. Router calls `pool.swap(B, zeroForOne, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, B, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → no revert.
8. User B's swap executes successfully, bypassing the per-user allowlist entirely.

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

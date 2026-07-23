Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address as Swapper, Allowing Any User to Bypass the Swap Allowlist on Curated Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` always sets to `msg.sender` — the direct pool caller. When users route through `MetricOmmSimpleRouter`, the router is the direct caller, so the extension checks whether the **router** is allowlisted rather than the **end user**. A pool admin who allowlists the router to enable standard periphery usage inadvertently opens the curated pool to every user who routes through `MetricOmmSimpleRouter`, completely defeating the allowlist's purpose.

## Finding Description
**Root cause — `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:** [1](#0-0) 

**`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router when the periphery is used:** [3](#0-2) 

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router `msg.sender` inside the pool:** [4](#0-3) 

**The same structural problem applies to the multi-hop `exactInput` path:** [5](#0-4) 

**Exploit flow:**
1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and calls `setAllowedToSwap(pool, allowedUser, true)`.
2. Pool admin calls `setAllowedToSwap(pool, address(router), true)` so that `allowedUser` can use the standard periphery.
3. `attacker` (not allowlisted) calls `router.exactInputSingle(...)`.
4. Inside `pool.swap`, `msg.sender == router`, so `_beforeSwap(router, ...)` is called.
5. `allowedSwapper[pool][router] == true` → check passes → attacker swaps successfully.

No existing guard prevents this: `setAllowedToSwap` accepts any address without distinguishing routers from end users, and the extension has no mechanism to decode or verify the actual originating user.

## Impact Explanation
A curated pool protected by `SwapAllowlistExtension` is intended to restrict swaps to a specific set of addresses. Once the router is allowlisted (a necessary operational step for any pool admin who wants allowlisted users to use the standard periphery), the guard collapses: any address can swap on the curated pool by routing through `MetricOmmSimpleRouter`. LP funds in the curated pool are exposed to unrestricted adverse flow — toxic order flow that the allowlist was designed to exclude — causing direct LP loss. This is a broken core pool functionality / admin-boundary break with direct fund impact, meeting the High/Critical threshold.

## Likelihood Explanation
The trigger is a natural and expected pool admin action: allowlisting the router. The admin has no on-chain signal that doing so opens the gate to all users, because `setAllowedToSwap` accepts any address without distinction. Once the router is allowlisted, the bypass is permissionless, requires no further privileged action, and is repeatable by any address.

## Recommendation
The extension must check the actual end user, not the intermediary contract. Two viable approaches:

1. **Trusted-forwarder pattern**: The router encodes the real user's address in `extensionData`; the extension decodes and checks it only when `sender` is a known router. This requires the extension to maintain a router registry.
2. **Pool-level fix**: `MetricOmmPool.swap` could accept an explicit `originator` argument that the router populates with `msg.sender` before calling the pool, and pass that to extensions instead of (or in addition to) `msg.sender`.

## Proof of Concept
```solidity
// Pool admin sets up a curated pool with SwapAllowlistExtension.
// Only `allowedUser` should be able to swap.
extension.setAllowedToSwap(pool, allowedUser, true);

// Pool admin also allowlists the router so allowedUser can use the periphery.
extension.setAllowedToSwap(pool, address(router), true);

// attacker is NOT allowlisted.
// Direct swap reverts:
vm.prank(attacker);
pool.swap(...);  // reverts: NotAllowedToSwap (allowedSwapper[pool][attacker] == false)

// But router-mediated swap succeeds:
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: attacker,
    ...
}));
// Passes: allowedSwapper[pool][router] == true
// attacker swaps successfully on the curated pool.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```

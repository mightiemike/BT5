All four files are confirmed in the repository. Let me verify the exact code paths before rendering a verdict.

**Confirmed code path:**

1. `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

2. `ExtensionCalling._beforeSwap` forwards `sender` unchanged to the extension: [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = whoever called `pool.swap()`: [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` to the pool: [4](#0-3) 

The router passes `params.extensionData` verbatim from the user — there is no mechanism to inject the originating user's address into the `sender` slot seen by the extension. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) [6](#0-5) 

The actor binding mismatch is real and confirmed. The finding meets the admin-boundary-break impact gate: an unprivileged user bypasses a pool admin's access control policy via a public, permissionless entry point.

---

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End-User, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is intended to restrict swaps on curated pools to a set of approved addresses. However, `beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()` — the immediate caller. When swaps are routed through `MetricOmmSimpleRouter`, the router becomes that immediate caller. If the router is allowlisted (the natural production configuration for router-mediated access), every user who routes through it bypasses the per-user allowlist entirely, regardless of individual approval status.

## Finding Description
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, passing the immediate caller as `sender`. `ExtensionCalling._beforeSwap` forwards this value unchanged via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()`, the router is `msg.sender` to the pool, so `sender` seen by the extension equals the router address — not the originating user. The router passes `params.extensionData` verbatim from the caller with no mechanism to inject the originating user into the `sender` slot. If the pool admin has called `setAllowedToSwap(pool, router, true)` — the expected setup to enable router-mediated swaps for approved users — the check `allowedSwapper[pool][router]` passes for every caller of the router, including non-approved addresses.

## Impact Explanation
A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to known counterparties (KYC'd addresses, institutional partners, whitelisted market makers). The admin allowlists the router so approved users can trade conveniently through it. Any non-approved address can then call `MetricOmmSimpleRouter.exactInputSingle` targeting that pool and execute swaps freely. The pool's core access control is broken for every swap entering through the router — the primary supported public entry point — constituting an admin-boundary break where an unprivileged path defeats a pool admin's explicitly configured access policy.

## Likelihood Explanation
The only precondition is that the pool admin has allowlisted the router — a natural and expected configuration for any production pool that wants to support router-mediated swaps for its approved users. The router is a public, permissionless contract. No privileged access, special tokens, flash loans, or malicious setup is required. Any address that knows the pool address can call `exactInputSingle` and bypass the allowlist immediately and repeatably.

## Recommendation
The `beforeSwap` hook must gate the economic actor (the end-user), not the immediate caller. The preferred fix is for the router to encode `msg.sender` into `extensionData` under a documented convention, and for `SwapAllowlistExtension.beforeSwap` to decode and check that field when `sender` is a known router. Alternatively, the pool's `swap()` interface should expose the originating user as a distinct parameter separate from `msg.sender`, and the extension should check that field. A third option is to never allowlist the router at the pool level and instead enforce the allowlist inside the router before calling the pool — but this requires the router to be upgraded and trusted as an enforcement point.

## Proof of Concept
```solidity
// Pool admin deploys pool with SwapAllowlistExtension and allowlists the router
extension.setAllowedToSwap(address(pool), address(router), true);
// Alice is individually approved
extension.setAllowedToSwap(address(pool), alice, true);

// Attacker (NOT allowlisted) — direct swap reverts:
vm.prank(attacker);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(attacker, true, 1000, type(uint128).max, "", "");

// Router-mediated swap succeeds — allowlist bypassed:
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Swap executes: sender seen by extension = router (allowlisted), not attacker
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```

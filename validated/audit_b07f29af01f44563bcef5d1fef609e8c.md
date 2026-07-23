Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any Caller to Bypass Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates the call, `sender` is the router address, not the originating user. A pool admin who allowlists the router to support router-based trading inadvertently grants every unprivileged user the ability to bypass the swap allowlist entirely.

## Finding Description

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool: [1](#0-0) 

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that `sender` value unchanged into the ABI-encoded extension call: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router itself `msg.sender` inside the pool: [4](#0-3) 

The full call chain is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ...)          // msg.sender in pool = router
     → _beforeSwap(sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router]       // checks router, not user
```

The pool admin must allowlist the router to permit any allowlisted user to trade via the router. Once the router is allowlisted, the extension's check passes for every caller regardless of their individual allowlist status, because the extension only ever sees the router address as `sender`. No existing guard in the extension, pool, or router prevents this — the extension has no mechanism to distinguish the originating user from the immediate `pool.swap()` caller.

## Impact Explanation

A pool deployed with `SwapAllowlistExtension` as a `beforeSwap` hook is intended to restrict trading to specific counterparties (e.g., KYC-gated or institutional pools). Once the router is allowlisted — the necessary operational step to support router-based trading for any allowlisted user — every unprivileged user can execute full swaps against the pool's liquidity. LP funds in the curated pool are exposed to unauthorized traders, breaking the core access-control invariant the extension was deployed to enforce. This constitutes a broken core pool functionality causing loss of LP assets and an admin-boundary break where an unprivileged path bypasses a factory/pool role check.

## Likelihood Explanation

Both required conditions are part of the normal, non-malicious deployment and operation lifecycle:
1. A pool deployed with `SwapAllowlistExtension` as a `beforeSwap` hook — a supported production extension.
2. The pool admin allowlisting the router — the only way to allow allowlisted users to trade via the router.

No privileged escalation or malicious setup is required from the attacker. Any user with token approval can exploit this once the router is allowlisted. The scenario is repeatable and requires no special timing or state.

## Recommendation

The extension must gate on the economic actor (the end user), not the immediate `pool.swap()` caller. The cleanest fix is for `MetricOmmSimpleRouter` to encode `msg.sender` into `extensionData` before forwarding the call, and for `SwapAllowlistExtension.beforeSwap` to decode and check that value when `extensionData` is present. Alternatively, document that `SwapAllowlistExtension` is incompatible with router intermediaries and revert if `sender` is a known router address. Checking `recipient` instead of `sender` is insufficient for multi-hop or contract-recipient cases.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  pool admin: setAllowedToSwap(pool, router, true)   // allowlist the router
  pool admin: setAllowedToSwap(pool, alice, true)    // allowlist alice
  bob = non-allowlisted user

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  → router calls pool.swap(bob, true, X, ...)
      msg.sender in pool = router
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
      allowedSwapper[pool][router] == true  ✓ passes
  → swap executes for bob despite bob not being allowlisted

Result:
  bob successfully swaps against the curated pool,
  bypassing the intended access control.
  LP funds are exposed to unauthorized trading.
```

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

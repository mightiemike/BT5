Audit Report

## Title
SwapAllowlistExtension Gates Router Address Instead of End User, Allowing Any User to Bypass the Allowlist via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed from the pool, which equals `msg.sender` at the pool call site — the router's address, not the originating user. Any pool admin who allowlists the router (required for router-mediated swaps to function) simultaneously opens the gate to every address on the network, completely defeating the allowlist's purpose.

## Finding Description

**Root cause — wrong identity checked:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist: [3](#0-2) 

In this check, `msg.sender` is the pool and `sender` is whoever called `pool.swap` — the router when the user goes through `MetricOmmSimpleRouter`. The actual end user (`msg.sender` of `exactInputSingle`) is never visible to the extension.

**Router call path — original caller is lost:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no originator forwarding: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap` as `msg.sender == router`. [5](#0-4) [6](#0-5) 

**Exploit flow:**

1. Pool P is deployed with `SwapAllowlistExtension` E.
2. Admin calls `E.setAllowedToSwap(P, alice, true)` — alice is KYC'd.
3. Admin calls `E.setAllowedToSwap(P, router, true)` — required for alice to use the standard router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})`.
5. Router calls `P.swap(recipient, ...)` — `msg.sender` at pool = router.
6. Pool calls `_beforeSwap(router, ...)`.
7. Extension checks `allowedSwapper[P][router]` → `true`.
8. Swap executes for bob with no revert.

**Existing guards are insufficient:** The only guard is the `allowedSwapper[msg.sender][sender]` check in `beforeSwap`. There is no mechanism to recover the original caller from the router's call context, and no `originator` field is passed through the extension interface. [7](#0-6) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC-verified users, protocol-internal actors, or whitelisted market makers). Once the router is allowlisted — the only way to let any allowlisted user trade through the standard periphery — the gate is open to the entire public. Non-allowlisted users can execute swaps at oracle-anchored prices, draining LP value or extracting arbitrage that the pool's curation policy was designed to prevent. This constitutes a broken core pool invariant (curated access) and direct loss of LP principal. This meets the "Broken core pool functionality causing loss of funds" and "Admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation

Any pool that (a) deploys `SwapAllowlistExtension` and (b) expects users to interact through `MetricOmmSimpleRouter` is immediately vulnerable. The router is the standard, documented periphery entry point. A pool admin who follows the normal integration path will allowlist the router, unknowingly opening the bypass. The attacker requires no special role, no privileged setup, and no non-standard token — a single call to `exactInputSingle` suffices. The condition is trivially reachable by any unprivileged address.

## Recommendation

The extension must gate the **economic actor**, not the immediate pool caller. Two complementary fixes:

1. **Pass the original caller through the router.** The router should forward `msg.sender` to the pool (e.g., via a dedicated `originator` field in the swap call or via `extensionData`), and the pool should pass it as a separate argument to extensions.

2. **Check `originator` in the allowlist extension.** `SwapAllowlistExtension.beforeSwap` should check the originator address when the immediate sender is a known periphery contract, or always check the originator when it is provided.

Short-term mitigation: document that pools using `SwapAllowlistExtension` **must not** allowlist the router, and that allowlisted users must call the pool directly. This is a severe UX restriction that underscores the need for the structural fix.

## Proof of Concept

```
Setup:
  - Pool P deployed with SwapAllowlistExtension E
  - Pool admin calls E.setAllowedToSwap(P, alice, true)   // alice is KYC'd
  - Pool admin calls E.setAllowedToSwap(P, router, true)  // needed for alice to use router
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient, ...) — msg.sender at pool = router
  3. Pool calls _beforeSwap(router, ...)
  4. Extension checks allowedSwapper[P][router] → true
  5. Swap executes for bob with no revert

Result:
  bob trades on a curated pool that was supposed to block him,
  bypassing the allowlist entirely.

Foundry test outline:
  - Deploy SwapAllowlistExtension, pool with extension configured
  - setAllowedToSwap(pool, router, true); setAllowedToSwap(pool, alice, true)
  - vm.prank(bob); router.exactInputSingle(...)
  - Assert swap succeeds (no NotAllowedToSwap revert)
  - Assert bob received output tokens despite not being allowlisted
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

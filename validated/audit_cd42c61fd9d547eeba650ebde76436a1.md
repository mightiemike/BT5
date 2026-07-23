Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of End User, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` populates with `msg.sender` — the immediate caller of the pool. When `MetricOmmSimpleRouter` intermediates a swap, `sender` is the router contract address, not the end user. A pool admin who allowlists the router to enable router-based access for curated users inadvertently grants every user on the network the ability to bypass the allowlist entirely.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` verbatim to every registered extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value, checking `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

The pool's `swap()` signature has no explicit `originator` parameter — the only identity it can report to extensions is `msg.sender`: [5](#0-4) 

This creates two broken states:

| Admin configuration | Outcome |
|---|---|
| Allowlists individual users, not the router | Allowlisted users **cannot** swap through the router (broken core flow) |
| Allowlists the router to enable router-based swaps | **All** users bypass the allowlist through the router |

The second state is the fund-impacting path: the extension evaluates `allowedSwapper[pool][router] == true` and passes for every caller, regardless of who initiated the call through the router. The extension has no visibility into the end user's identity.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter` and trade against the pool's liquidity without authorization. LP funds are exposed to toxic flow or policy-violating counterparties that the pool admin explicitly intended to exclude. This matches **"Broken core pool functionality causing loss of funds"** and **"allowlist bypass through a public router path."**

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported swap entrypoint in the periphery layer. A pool admin who configures `SwapAllowlistExtension` and wants allowlisted users to be able to use the router must allowlist the router — there is no other mechanism. This is a natural, expected configuration step. The bypass is therefore reachable on any curated pool that supports router-based access, triggered by any unprivileged user calling the public router.

## Recommendation
The extension must gate on the **economic actor**, not the immediate pool caller. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes the actual `msg.sender` (the end user) into `extensionData` before calling `pool.swap()`. The extension decodes and verifies that address against the allowlist. The pool admin must trust the router to populate this field honestly, which is acceptable for a factory-registered periphery contract.

2. **Add an explicit `originator` parameter to `pool.swap()`**: The pool accepts an `originator` address alongside `recipient`, passes it to extensions, and the extension checks `allowedSwapper[pool][originator]`. The router populates `originator = msg.sender` (the end user).

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Admin calls swapExtension.setAllowedToSwap(pool, user1, true)
   — intending only user1 to trade.
3. Admin calls swapExtension.setAllowedToSwap(pool, router, true)
   — to let user1 reach the pool through the router.
4. user2 (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(...).
5. Router calls pool.swap(...) — pool records msg.sender = router.
6. Pool calls extension.beforeSwap(router, ...).
7. Extension evaluates: allowedSwapper[pool][router] == true → PASS.
8. user2 completes the swap on the curated pool without authorization.
```

The invariant "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it" is broken: `SwapAllowlistExtension` enforces the policy correctly for direct pool calls but fails open for every call routed through `MetricOmmSimpleRouter`. [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-225)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

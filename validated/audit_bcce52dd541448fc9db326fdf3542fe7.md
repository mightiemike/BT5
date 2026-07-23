Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When any user routes through `MetricOmmSimpleRouter`, `sender` equals the router contract address, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for curated users inadvertently opens the pool to every address on-chain, completely nullifying the per-user allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

From the pool's perspective, `msg.sender` = router address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The same applies to `exactInput`, `exactOutput`, and `exactOutputSingle`, all of which call `pool.swap()` as `msg.sender = router`. [5](#0-4) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the economically relevant actor receiving LP shares), not on `sender` (the LiquidityAdder contract): [6](#0-5) 

`SwapAllowlistExtension` does not follow this pattern, creating an asymmetric and exploitable design.

## Impact Explanation
If the pool admin allowlists the router (a natural operational step when onboarding curated users who expect to use the standard periphery), any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` or any other router entry point and trade against the pool's liquidity. The allowlist protection is completely nullified. LP funds are exposed to counterparties the pool was explicitly designed to exclude, which constitutes direct LP principal loss on pools that rely on counterparty curation for their risk model. This meets the Critical/High threshold for broken core pool functionality causing loss of funds.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical swap interface expected by end users. A pool admin deploying a curated pool who wants allowlisted users to access it through the standard router will naturally add the router to the allowlist. The mistake is not obvious because the admin is adding a trusted periphery contract, not an arbitrary address. Once the router is allowlisted, the bypass is immediately available to any on-chain address with no further preconditions and is repeatable for arbitrary swap amounts.

## Recommendation
Pass the original end-user address through the extension pipeline. Two viable approaches:

1. **Router-injected identity via `extensionData`:** Have the router encode `msg.sender` (the actual user) into `extensionData` before forwarding to the pool. `SwapAllowlistExtension` decodes and checks this value instead of `sender`. The extension must verify the payload came from a trusted router (e.g., check `sender == trustedRouter`).

2. **Check `recipient` instead of `sender`:** For single-hop swaps where the user is also the recipient, checking `recipient` would gate the correct actor. This does not generalise to multi-hop paths where intermediate recipients are the router itself.

Approach 1 is the cleanest fix, preserving the extension's ability to enforce per-user policies regardless of which periphery contract is used, consistent with how `DepositAllowlistExtension` handles the analogous `owner` vs `sender` distinction.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, userA, true)      // allowlist userA for direct swaps
  admin calls setAllowedToSwap(pool, address(router), true)  // allowlist router for router-mediated swaps

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle({
        pool: pool,
        recipient: userB,
        zeroForOne: true,
        amountIn: X,
        ...
    })

  pool.swap() is called with msg.sender = router
  _beforeSwap(sender=router, ...)
  SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  Swap executes; userB receives output tokens

Result:
  userB bypassed the per-user allowlist entirely.
  Any address can repeat this for arbitrary swap amounts.
  LP funds are exposed to all counterparties, not just curated ones.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

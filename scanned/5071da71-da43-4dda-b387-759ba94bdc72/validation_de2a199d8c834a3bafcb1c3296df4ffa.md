### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` mediates a swap, `sender` equals the router address, not the originating user. A pool admin who allowlists the router so that legitimate users can reach the pool through the router inadvertently grants every address on-chain the ability to bypass the allowlist by routing through the same public contract.

### Finding Description
In `SwapAllowlistExtension.beforeSwap`, the identity check is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the pool calls the extension), and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the `pool.swap()` call itself. [1](#0-0) 

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender`:

```solidity
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()`, `msg.sender` of that call is the router contract:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
``` [3](#0-2) 

So the allowlist lookup becomes `allowedSwapper[pool][router]`. If the pool admin allowlists the router address (which is necessary for any allowlisted user to use the router), the check passes for **every** caller of the router, regardless of whether that caller is on the allowlist.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` with `msg.sender` = router. [4](#0-3) 

The `multicall` function on the router uses `delegatecall`, so `msg.sender` is preserved as the router address in all delegated swap calls, providing no additional protection. [5](#0-4) 

### Impact Explanation
Any user can bypass the swap allowlist on a restricted pool by routing through `MetricOmmSimpleRouter`. The pool admin's access-control intent — restricting which addresses may trade — is completely nullified. Unauthorized addresses can execute swaps, draining pool liquidity at oracle prices, violating regulatory or compliance constraints the pool was configured to enforce, and undermining any stop-loss or velocity guard that depends on the allowlist being the first line of defense.

### Likelihood Explanation
Medium. The trigger requires the pool admin to allowlist the router address. This is the natural, expected action for any pool admin who wants allowlisted users to be able to use the standard periphery router rather than calling the pool directly. The admin has no indication from the extension's interface or documentation that allowlisting the router collapses the per-user gate to a per-contract gate. The router is a public, permissionless contract, so once it is allowlisted, every address on-chain gains access.

### Recommendation
The `SwapAllowlistExtension` must gate the **original user**, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Do not allowlist the router.** Document clearly that pools using `SwapAllowlistExtension` are incompatible with `MetricOmmSimpleRouter` unless every user is individually allowlisted. Allowlisting the router address must be explicitly prohibited or warned against.

2. **Forward the originating user through the router.** The router could pass the original `msg.sender` as `callbackData` or `extensionData`, and the extension could decode and verify it. This requires a coordinated change to the router and extension interface.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
  admin calls setAllowedToSwap(pool, router, true)  // to let alice use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})

Execution trace:
  router.exactInputSingle()          msg.sender = bob
    pool.swap()                      msg.sender = router
      _beforeSwap(sender=router, ...)
        SwapAllowlistExtension.beforeSwap(sender=router, ...)
          allowedSwapper[pool][router] == true  ← passes
  bob's swap executes successfully
```

`allowedSwapper[pool][router]` is `true` because the admin set it to allow alice to use the router. Bob's address is never checked. The allowlist is bypassed. [1](#0-0) [2](#0-1) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
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

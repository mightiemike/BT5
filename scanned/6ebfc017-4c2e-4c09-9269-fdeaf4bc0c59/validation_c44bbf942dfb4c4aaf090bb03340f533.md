### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the pool's `msg.sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the end user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged address can bypass the per-user allowlist by routing through the same public router.

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
2. Router calls `pool.swap(recipient, ...)` — the pool sees `msg.sender = router`.
3. Pool calls `_beforeSwap(msg.sender=router, ...)`.
4. `ExtensionCalling._beforeSwap` forwards `sender=router` to the extension.
5. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

**The invariant break:** The extension cannot simultaneously (a) allow allowlisted users to swap through the router and (b) block non-allowlisted users from doing the same. If the admin adds the router to the allowlist so that legitimate users can use the standard periphery, the check passes for every caller regardless of their identity, because the router address is the only identity the extension ever sees.

**Relevant code:**

`MetricOmmPool.swap` passes `msg.sender` as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `sender` (the router) against the per-pool allowlist: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

### Impact Explanation

A curated pool that uses `SwapAllowlistExtension` to restrict trading to a set of approved counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker receives output tokens from the pool at oracle-anchored prices without being on the allowlist. This is a direct policy bypass that allows unauthorized parties to drain liquidity from a pool whose LP depositors expected access control to be enforced.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery swap path. Any pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. Once that is done, the bypass is available to every address with no special privileges, no malicious setup, and no non-standard tokens. The trigger is a single public `exactInputSingle` call.

### Recommendation

The extension must gate on the **economic actor** (the end user who initiated the transaction), not on the intermediate contract that called the pool. Two approaches:

1. **Pass the original initiator through the pool.** Add an `initiator` field to the swap call or extension data so the router can forward `msg.sender` (the real user) explicitly, and have the extension check that field instead of `sender`.
2. **Check `sender` only for direct pool calls; require the router to forward user identity in `extensionData`.** The extension decodes the real user from `extensionData` when `sender` is a known router, and falls back to `sender` for direct calls.

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted.
swapExtension.setAllowedToSwap(address(pool), alice, true);
// Admin also allowlists the router so alice can use it:
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (bob, NOT allowlisted) calls the router directly:
vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(pool),
    tokenIn:         address(token0),
    tokenOut:        address(token1),
    zeroForOne:      true,
    amountIn:        1_000,
    amountOutMinimum: 0,
    recipient:       bob,
    deadline:        block.timestamp + 1,
    priceLimitX64:   0,
    extensionData:   ""
}));
// Pool's msg.sender = router → allowedSwapper[pool][router] = true → passes.
// Bob receives token1 despite not being on the allowlist.
``` [6](#0-5) [7](#0-6)

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

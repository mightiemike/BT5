### Title
SwapAllowlistExtension Gates on Router Address Instead of Originating User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user swaps through `MetricOmmSimpleRouter`, the pool sees the router as `msg.sender`, not the original user. If the pool admin allowlists the router to enable router-mediated swaps, any non-allowlisted user can bypass the allowlist by routing through the same public router contract.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (correct), and `sender` is the first argument forwarded from `ExtensionCalling._beforeSwap`, which is `msg.sender` of the pool's `swap` call. [1](#0-0) 

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [3](#0-2) 

So `msg.sender` of the pool's `swap` call is the **router address**, not the original user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

This creates an impossible choice for the pool admin:

1. **Do not allowlist the router** → allowlisted users cannot use `MetricOmmSimpleRouter` at all (every router-mediated swap reverts `NotAllowedToSwap`)
2. **Allowlist the router** → any user, allowlisted or not, can bypass the restriction by calling the router

The same problem applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap` with `msg.sender = router`. [4](#0-3) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on the `owner` argument (the position owner), not the `sender` (the immediate caller), so it is not affected by this issue: [5](#0-4) 

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses. If the admin allowlists the router (the natural step to allow allowlisted users to use the standard periphery), any unprivileged user can bypass the restriction by calling `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant). The non-allowlisted user trades on a pool that was supposed to be restricted, breaking the core access-control invariant of the extension. Depending on the pool's purpose (e.g., institutional-only, regulatory-gated, or front-running-prevention pools), this can result in direct loss of LP value or protocol fee leakage to unauthorized counterparties.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, publicly documented periphery swap entry point. Any user who observes that a pool uses `SwapAllowlistExtension` and that the router is allowlisted can immediately exploit this by routing through the router. No privileged access, special tokens, or complex setup is required. The trigger is a single public call. [6](#0-5) 

### Recommendation

The `SwapAllowlistExtension` must gate on the **originating user**, not the immediate caller of the pool. Two approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` into `extensionData` and have the extension decode and check it. The pool admin would allowlist individual users, and the extension would verify the decoded address regardless of which periphery contract mediated the call.

2. **Separate router-aware allowlist**: Add a second mapping `allowedSwapperViaRouter[pool][user]` and have the router pass the original user in `extensionData` for the extension to verify.

The current design where `sender` is the immediate pool caller cannot simultaneously allow router-mediated swaps for allowlisted users while blocking them for non-allowlisted users.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists alice directly: allowedSwapper[pool][alice] = true
  - Pool admin allowlists the router so alice can use it: allowedSwapper[pool][router] = true

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(...) with msg.sender = router
  - _beforeSwap passes sender = router to SwapAllowlistExtension.beforeSwap
  - Extension checks allowedSwapper[pool][router] == true → passes
  - bob's swap executes on the curated pool despite not being allowlisted

Result:
  - bob bypasses the swap allowlist entirely
  - The allowlist invariant "only alice can swap" is broken
``` [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the actual user. If the pool admin allowlists the router (the natural action to enable router-mediated swaps), every user—including those not individually allowlisted—can bypass the per-user gate.

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, forwarding its own `msg.sender` as the `sender` argument to every configured extension. [1](#0-0) 

`ExtensionCalling._beforeSwap` relays that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever was passed in—the router when the user goes through `MetricOmmSimpleRouter`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making itself the `msg.sender` the pool sees: [4](#0-3) 

The router stores the actual user's address only in transient storage for the payment callback; it is never forwarded to the pool or the extension: [5](#0-4) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` parameter (the position owner), which the liquidity adder correctly sets to the actual user. The swap path has no equivalent mechanism—the extension has no way to recover the real user's identity from the router call. [6](#0-5) 

The NatSpec for `SwapAllowlistExtension` states its purpose is to gate "`swap` by swapper address", meaning the actual human swapper, not the intermediary router. [7](#0-6) 

### Impact Explanation

A pool admin who configures a curated pool with `SwapAllowlistExtension` and then allowlists the router (the expected action to support the standard periphery flow) inadvertently opens the pool to **all** users. Any address can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will pass because it sees the allowlisted router, not the individual caller. The per-user allowlist is completely nullified for router-mediated swaps. This breaks the core security model of curated pools and constitutes a broken core pool functionality with direct policy-bypass consequences for LP assets and pool access control.

### Likelihood Explanation

The trigger is a normal, expected admin action: allowlisting the router so that users can interact through the standard periphery. Any pool that uses `SwapAllowlistExtension` and also wants to support `MetricOmmSimpleRouter` will hit this condition. No special permissions, malicious setup, or non-standard tokens are required—only a valid semi-trusted admin enabling the supported public entrypoint.

### Recommendation

The `beforeSwap` hook should gate the economically relevant actor. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.
2. **Mirror the deposit pattern**: Introduce a dedicated `swapper` field in the swap call (analogous to `owner` in `addLiquidity`) so the pool can forward the real initiator independently of `msg.sender`.

Until fixed, pool admins must choose between supporting the router (opening the pool to all users) or restricting to direct `pool.swap()` calls only (breaking the standard periphery flow).

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, router, true)   // enable router-mediated swaps
  admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  router calls pool.swap(recipient, ...) — msg.sender = router
  pool calls _beforeSwap(router, ...)
  SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  swap executes — attacker's trade settles despite never being individually allowlisted
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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

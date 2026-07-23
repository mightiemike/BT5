### Title
SwapAllowlistExtension gates the router address instead of the real user on router-mediated swaps, enabling allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool. The pool passes `msg.sender` of the `pool.swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`, binding the allowlist check to the wrong actor.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces per-pool swap access control by checking the `sender` parameter: [1](#0-0) 

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

`msg.sender` here is the pool (the contract that calls the extension). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which the pool populates with its own `msg.sender` — i.e., whoever called `pool.swap()`. [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` directly: [3](#0-2) 

The pool's `msg.sender` at that point is the router contract. The pool therefore passes `router` as `sender` to `_beforeSwap`. The extension evaluates:

```
allowedSwapper[pool][router]   // checked
allowedSwapper[pool][user]     // never checked
```

The same misbinding occurs for `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

---

### Impact Explanation

**Scenario A — router is allowlisted (pool admin adds router to allowlist to support periphery users):**  
Any unpermissioned user can bypass the curated allowlist by calling `router.exactInputSingle(...)`. The extension sees `allowedSwapper[pool][router] = true` and passes every swap regardless of who the real initiator is. This is a complete allowlist bypass with direct fund-impact: non-allowlisted users trade on a pool that was explicitly restricted to a curated set.

**Scenario B — router is not allowlisted:**  
Allowlisted users who attempt to swap through the router are permanently blocked because the extension sees `allowedSwapper[pool][router] = false`. Core swap functionality is broken for the supported periphery path.

Both outcomes break the invariant that the allowlist gates the economically relevant actor.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary public swap entrypoint documented in the protocol.
- Pool admins who want to support normal user flows will naturally allowlist the router, triggering Scenario A.
- Any user can call the router permissionlessly; no special role or setup is required beyond knowing the pool address.
- The misbinding is structural and present on every router-mediated swap path (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

---

### Recommendation

The pool must forward the original initiator's address as `sender`, not its own `msg.sender`. One approach is for the router to pass the real user address through the `callbackData` or `extensionData` channel and have the extension decode it. Alternatively, the pool can expose a dedicated `swapWithSender(address realSender, ...)` entry point restricted to trusted periphery contracts, and the extension can verify the caller is a trusted router before accepting the forwarded identity.

The `DepositAllowlistExtension` avoids this problem because `addLiquidity` takes an explicit `owner` parameter that the caller controls; the swap path has no equivalent explicit-sender parameter. [5](#0-4) 

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Call `extension.setAllowedToSwap(pool, router, true)` (pool admin allowlists the router to support periphery).
3. As an address that is **not** in `allowedSwapper[pool]`, call `router.exactInputSingle(...)` targeting the pool.
4. The extension receives `sender = router`, finds `allowedSwapper[pool][router] = true`, and allows the swap.
5. The non-allowlisted user successfully swaps on a curated pool, bypassing the intended access control. [6](#0-5) [7](#0-6)

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

### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Making the Allowlist Bypassable or Broken for Router-Mediated Swaps - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using the `sender` argument forwarded by the pool, which equals `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the actual end user. The allowlist therefore checks the wrong actor, making it impossible to correctly enforce per-user swap restrictions on router-mediated paths.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`msg.sender` here is the pool (correct, used as the mapping key). `sender` is the argument the pool passes to `_beforeSwap`, which the pool derives from its own `msg.sender` — the direct caller of `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [2](#0-1) 

The pool's `msg.sender` is the router contract. The pool therefore passes the router's address as `sender` to `_beforeSwap`: [3](#0-2) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` — in every case the pool's `msg.sender` is the router, so `sender` in the extension is always the router address, never the originating user. [4](#0-3) 

### Impact Explanation

There are two mutually exclusive failure modes, both fund-impacting:

**Mode A — Allowlist bypass:** If the pool admin allowlists the router address (the natural fix to let router users trade), then every user — including those the admin explicitly excluded — can bypass the per-user restriction by routing through `MetricOmmSimpleRouter`. The curated pool's access control is completely defeated for all router-mediated swaps.

**Mode B — Broken core functionality:** If the pool admin allowlists only individual user addresses (the intended design), then those allowlisted users cannot use the router at all. Their router calls revert with `NotAllowedToSwap` because `allowedSwapper[pool][router]` is false. The primary public swap entrypoint is unusable for the pool's legitimate participants.

There is no configuration of the allowlist that correctly enforces per-user restrictions while also permitting router-mediated swaps.

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` to restrict swaps to a curated set of addresses immediately encounters this issue the first time an allowlisted user attempts to swap through the router. The router is the primary public entrypoint documented in the periphery. The trigger requires no privileged action beyond the normal pool setup and a standard router call.

### Recommendation

The pool should pass the originating user's address as `sender` rather than its own `msg.sender`. One approach is for the router to supply the real user address in the `extensionData` payload and for the extension to decode it — but this is forgeable by any direct caller. The more robust fix is for the pool's `swap` function to accept an explicit `sender` parameter (similar to how `addLiquidity` accepts an explicit `owner`), so the router can forward `msg.sender` (the real user) and the extension can trust it because the pool is the one encoding it.

Alternatively, `SwapAllowlistExtension` can check `recipient` instead of `sender` when the pool is called through a known router, but this requires the extension to be aware of trusted router addresses, which introduces its own complexity.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowlisted swapper.
3. Alice calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. The router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension checks `allowedSwapper[pool][router]` → `false` → reverts `NotAllowedToSwap`.
7. Alice's swap fails despite being explicitly allowlisted.

**Bypass variant:**

1. Pool admin additionally calls `setAllowedToSwap(pool, router, true)` to fix Alice's problem.
2. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
3. Extension checks `allowedSwapper[pool][router]` → `true` → swap succeeds.
4. Bob bypasses the allowlist entirely. [1](#0-0) [5](#0-4) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

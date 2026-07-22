### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any user to bypass the swap allowlist via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for permitted users), every user who calls the router can bypass the allowlist entirely.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the **router**, not the end user: [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`. The actual user's address is never consulted. The same mismatch applies to every multi-hop path (`exactInput`, `exactOutput`, `exactOutputSingle`). [5](#0-4) 

### Impact Explanation

A pool admin who wants to restrict swaps to a curated set of counterparties (e.g., trusted market makers) and also wants those counterparties to be able to use the router must allowlist the router address. Once the router is allowlisted, **any** user who calls the router passes the allowlist check, because the extension only sees the router's address. The intended access boundary is completely nullified. Unauthorized users can trade at oracle-derived prices in a pool that was designed to exclude them, extracting value from LPs who deposited under the assumption that only trusted counterparties would trade.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap entry point. Any pool admin who deploys a curated pool with `SwapAllowlistExtension` and also wants to support router-mediated swaps for their allowlisted users will naturally allowlist the router. The bypass is then reachable by any unprivileged user with a single router call. No special permissions, flash loans, or unusual token behavior are required.

### Recommendation

The `beforeSwap` hook should gate on the **economically relevant actor** — the address that initiated the transaction and will pay for the swap — rather than the direct caller of `pool.swap()`. Two concrete options:

1. **Pass the original `msg.sender` through the router as a separate field in `extensionData`** and have the extension decode and verify it. This requires a convention between the router and the extension.
2. **Check `tx.origin` as a fallback** when `sender` is a known router. This is fragile and generally discouraged.
3. **Preferred**: redesign the hook signature so the pool passes both the direct caller (`sender`) and an authenticated "originator" field that the router populates via a signed or transient-storage mechanism, and have `SwapAllowlistExtension` gate on the originator.

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
   — intending to allow allowlisted users to reach the pool via the router.
3. attacker (not in the allowlist) calls:
     router.exactInputSingle(ExactInputSingleParams{
       pool: pool,
       recipient: attacker,
       zeroForOne: true,
       amountIn: X,
       ...
     })
4. router calls pool.swap(attacker, true, X, ...).
   pool sets sender = address(router).
   Extension checks allowedSwapper[pool][router] → true → swap proceeds.
5. attacker successfully swaps in a pool that was supposed to block them,
   receiving tokens at oracle-derived prices and extracting value from LPs.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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

### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User — Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. Any pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the pool to every user, defeating the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the pool's `msg.sender` the router, not the originating user: [4](#0-3) 

The same substitution occurs in every multi-hop `exactInput` leg: [5](#0-4) 

The result: the extension always checks the router's address against the allowlist, never the actual user's address. The `DepositAllowlistExtension` does not share this flaw because it gates on `owner`, which is an explicit parameter preserved through the liquidity adder path. [6](#0-5) 

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of counterparties. To allow those counterparties to use the standard router, the admin calls `setAllowedToSwap(pool, router, true)`. From that moment, every address on the network can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The pool's curated-access invariant is permanently broken for all subsequent swaps, and any user can drain liquidity at oracle prices the pool was not intended to serve to the general public.

---

### Likelihood Explanation

Allowlisting the router is the natural and expected action for any admin who wants to support the standard periphery. The router is the primary user-facing entry point documented by the protocol. No special knowledge or privileged access is required beyond a normal `exactInputSingle` call. The trigger is a single public transaction.

---

### Recommendation

The extension must gate on the economically relevant actor, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Pass the originating user through the pool.** Add an explicit `originator` field to the swap interface so the router can forward `msg.sender` before delegating to the pool. The extension then checks `originator` instead of `sender`.

2. **Check `sender` only when the caller is not a trusted router.** The extension can maintain a registry of trusted routers and, when `sender` is a known router, require the router to embed the real user address in `extensionData`.

The simplest correct fix mirrors the deposit path: the pool should accept an explicit `swapper` parameter (analogous to `owner` in `addLiquidity`) that the router sets to `msg.sender` before calling the pool, and the extension checks that field.

---

### Proof of Concept

1. Admin deploys a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users.
3. Attacker (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)`. Pool's `msg.sender` = router.
5. Pool calls `extension.beforeSwap(router, ...)`. Extension evaluates `allowedSwapper[pool][router]` → `true`.
6. Swap executes. Attacker receives output tokens at oracle prices on a pool that was never intended to serve them.
7. The allowlist provided zero protection; the attacker paid no fee tier premium and faced no curation barrier.

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

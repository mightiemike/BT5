Now I have all the information needed. Let me trace the exact call chain for both the swap and deposit allowlist paths.

**Swap allowlist path:**
- `MetricOmmSimpleRouter.exactInputSingle` → `pool.swap(recipient, ..., params.extensionData)` with `msg.sender = router`
- `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` where `msg.sender = router`
- `ExtensionCalling._beforeSwap` passes `sender = router` to `SwapAllowlistExtension.beforeSwap`
- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`

**Deposit allowlist path:**
- `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` → `pool.addLiquidity(owner, ...)`
- `DepositAllowlistExtension.beforeAddLiquidity` checks `allowedDepositor[msg.sender][owner]` = `allowedDepositor[pool][owner]`

The deposit allowlist correctly gates `owner` (the position beneficiary), so it is **not** bypassable through the adder.

The swap allowlist gates `sender` which is the pool's `msg.sender`. When the router is used, `sender = router`, not the original user. This creates an impossible dilemma for pool admins:

- If the router is **not** allowlisted: router-mediated swaps fail for all users, including allowlisted ones.
- If the router **is** allowlisted: any user can bypass the swap allowlist by routing through the router.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing so.

---

### Title
Swap Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the pool's immediate `msg.sender` (`sender` parameter), not the originating user. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract. If the router is allowlisted on a curated pool (required for any router-mediated swap to work), every unprivileged user can bypass the swap allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

### Impact Explanation
Any user not on the swap allowlist can trade on a curated pool by calling any `exact*` function on `MetricOmmSimpleRouter`, provided the router is allowlisted. The pool admin has no way to simultaneously allow allowlisted users to use the router and block non-allowlisted users from doing the same. The allowlist policy is entirely defeated for the router path.

### Likelihood Explanation
The router is the primary supported swap interface. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address, which immediately opens the bypass to all users. This is a structural conflict with no workaround in the current design.

### Recommendation
Pass the originating user's address through the router to the pool, either as part of `callbackData` or a dedicated field, and have the pool forward it as a separate `originator` argument to extension hooks. Alternatively, `SwapAllowlistExtension.beforeSwap` could inspect `extensionData` for a signed originator claim, but this requires a protocol-level convention. The simplest fix is to add an `originator` field to the `beforeSwap` hook signature that the pool populates from a verified source rather than from `msg.sender`.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Allowlist only `alice` as a swapper on the pool.
3. Also allowlist the router address (required for `alice` to use the router).
4. Call `MetricOmmSimpleRouter.exactInputSingle` as `bob` (not on the allowlist).
5. The extension checks `allowedSwapper[pool][router]` — the router is allowlisted — and the call succeeds.
6. `bob` has traded on a pool that was supposed to block him.

The deposit allowlist (`DepositAllowlistExtension`) is **not** affected: it checks `owner` (the position beneficiary), which the liquidity adder passes correctly as the caller-supplied `owner` argument, not the adder's own address. [6](#0-5)

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

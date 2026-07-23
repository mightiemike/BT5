### Title
`SwapAllowlistExtension` checks router address instead of actual swapper when swaps route through `MetricOmmSimpleRouter`, enabling full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates the `sender` argument forwarded by the pool, which is `msg.sender` of the pool's `swap` call. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the allowlist checks the router address rather than the actual user. If the pool admin adds the router to the allowlist (the only way to let allowlisted users use the router), every unprivileged user can bypass the curated-pool restriction by routing through the same public router.

---

### Finding Description

**Call chain for a router-mediated swap:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()`.
2. Router calls `IMetricOmmPoolActions(pool).swap(recipient, ...)` — the router is now `msg.sender` to the pool.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)` where `msg.sender` is the **router**.
4. `ExtensionCalling._beforeSwap` encodes and dispatches `beforeSwap(sender=router, ...)` to the extension.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][sender]` — checking the **router**, not the user. [1](#0-0) [2](#0-1) [3](#0-2) 

**Contrast with `DepositAllowlistExtension`:** the deposit guard correctly ignores `sender` (the adder contract) and checks `owner` — the actual position owner passed explicitly by the liquidity adder. [4](#0-3) 

The swap allowlist has no equivalent "owner" parameter; it only receives `sender`, which collapses to the router address on every router-mediated call.

**The operational trap:** A pool admin who wants allowlisted users to be able to use the standard router must add the router to `allowedSwapper`. The moment they do, `allowedSwapper[pool][router] == true` and the check passes for **any** caller, because the pool always forwards `msg.sender = router` regardless of who initiated the transaction. [5](#0-4) 

The same issue applies to `exactInput` (multi-hop) and `exactOutputSingle` / `exactOutput`, all of which call `pool.swap()` directly with the router as `msg.sender`. [6](#0-5) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` is intended to restrict trading to a specific set of addresses (e.g., KYC'd users, whitelisted market makers). Once the pool admin adds the router to the allowlist — the only way to let allowlisted users trade through the standard periphery — the restriction is completely voided. Any unprivileged address can execute swaps against the pool by calling `exactInputSingle` or `exactInput` on the public router, receiving output tokens at oracle-anchored prices. This is a direct loss of the curation guarantee and allows unauthorized parties to drain pool liquidity at will.

---

### Likelihood Explanation

High. The `MetricOmmSimpleRouter` is the documented standard swap interface. Pool admins who deploy a `SwapAllowlistExtension` pool and want their allowlisted users to trade will inevitably add the router to the allowlist. The bypass requires no special knowledge beyond knowing the router address, which is a public deployment. Any user can exploit it in a single transaction.

---

### Recommendation

The swap allowlist must gate the **economic actor**, not the intermediary. Two viable approaches:

1. **Pass original caller through the router:** Add an `originator` field to the swap call or extension data that the router populates with `msg.sender` before calling the pool, and have `SwapAllowlistExtension` read that field. This requires a protocol-level convention.

2. **Mirror the deposit pattern:** Require the pool to expose a separate `swapOwner` parameter (analogous to `owner` in `addLiquidity`) that the router fills with the actual user, and have `SwapAllowlistExtension` check that field instead of `sender`.

Until fixed, pools that deploy `SwapAllowlistExtension` and add the router to the allowlist provide no meaningful access control.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to let allowlisted users use the router
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: attacker,
        zeroForOne: true,
        amountIn: X,
        ...
    })
  - Router calls pool.swap(attacker, true, X, ...)
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes; attacker receives output tokens.

Result: attacker, who is not on the allowlist, successfully swaps against the curated pool.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

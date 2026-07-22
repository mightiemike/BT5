### Title
`SwapAllowlistExtension` gates the router address instead of the originating user, allowing any caller to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router address to enable router-mediated swaps for their permitted users inadvertently opens the pool to every caller of the public router, defeating the per-user gate entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool and therefore the `sender` seen by the extension: [4](#0-3) 

The same substitution occurs for `exactInput` (intermediate hops use `address(this)` as payer), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

A pool admin who wants allowlisted users to be able to swap through the router has no option other than calling `setAllowedToSwap(pool, router, true)`. Once the router address is in the allowlist, the check `allowedSwapper[pool][router]` passes for every caller of the public, permissionless router — including users who were never individually allowlisted.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the LP share recipient), not on `sender` (the immediate caller): [6](#0-5) 

---

### Impact Explanation

Any non-allowlisted address can execute swaps on a pool that the admin intended to restrict to a specific set of users, simply by routing through `MetricOmmSimpleRouter`. The access-control invariant of `SwapAllowlistExtension` — "only addresses explicitly permitted by the pool admin may swap" — is broken for all router-mediated paths the moment the router is allowlisted. Pools designed for KYC-gated, institutional, or otherwise restricted participant sets are fully open to the public through the router.

---

### Likelihood Explanation

The trigger is a pool admin allowlisting the router address. This is the natural, expected action for any admin who wants their permitted users to be able to use the standard periphery router rather than calling the pool directly. The router is a public, production contract explicitly listed in the periphery. The admin has no alternative mechanism to grant router access to specific users without granting it to all users. The condition is therefore likely to occur in any real deployment that combines `SwapAllowlistExtension` with `MetricOmmSimpleRouter`.

---

### Recommendation

The extension must identify the originating user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **`extensionData` attestation**: Require the router to encode the original `msg.sender` into `extensionData` for each hop, and have `SwapAllowlistExtension` decode and check that address instead of (or in addition to) `sender`. The extension should reject calls where `sender` is the router but `extensionData` is empty or carries an unapproved address.

2. **Separate `originator` parameter**: Add an `originator` field to the `beforeSwap` hook signature so the pool can propagate the true end-user identity through the extension call chain, independent of the immediate caller.

Until one of these is implemented, pool admins must be warned that allowlisting the router address is equivalent to `setAllowAllSwappers(pool, true)`.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension (allowAll = false by default).
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the permitted user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // enable router for alice

Attack
──────
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, tokenIn: T0, amountIn: X, recipient: bob, ...})

5. Router calls pool.swap(bob, zeroForOne, X, limit, "", "")
   → msg.sender inside pool.swap() = router

6. pool._beforeSwap(sender=router, ...) is dispatched to SwapAllowlistExtension.

7. Extension evaluates:
       allowAllSwappers[pool]          → false
       allowedSwapper[pool][router]    → true   ← router was allowlisted in step 3

8. Check passes. Bob's swap executes successfully despite never being allowlisted.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [3](#0-2) [7](#0-6) [1](#0-0)

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

### Title
`SwapAllowlistExtension` Gates on Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` at the pool level. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the originating user. If the pool admin allowlists the router address (required to permit any router-mediated swaps), every user on the network can bypass the allowlist by routing through the router, defeating the entire purpose of the extension.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle(...)
         → IMetricOmmPoolActions(pool).swap(recipient, ..., extensionData)
              [msg.sender at pool = router address]
         → MetricOmmPool.swap passes msg.sender as `sender`
         → ExtensionCalling._beforeSwap(sender=router, ...)
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              checks: allowedSwapper[msg.sender/*pool*/][sender/*router*/]
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value the pool forwarded — the router address, not the originating user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no mechanism to forward the original `msg.sender`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**The bypass:**

A pool admin who wants to allow router-mediated swaps for their allowlisted users must call `setAllowedToSwap(pool, routerAddress, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** call that arrives through the router, regardless of who the originating user is. Any non-allowlisted user can call `router.exactInputSingle(...)` and the extension will approve the swap because it sees `sender = router`.

**The alternative is equally broken:** if the admin does not allowlist the router, then allowlisted users cannot use the router at all — the extension will reject the swap because `allowedSwapper[pool][router]` is false. This makes the router unusable on any allowlisted pool.

---

### Impact Explanation

**Direct loss of curation policy and LP principal.** The `SwapAllowlistExtension` is the mechanism by which pool admins restrict trading to vetted counterparties (e.g., KYC'd users, protocol-owned addresses, or specific market makers). When the bypass is active:

- Any unpermissioned user can execute swaps against the pool's liquidity, extracting value from LP positions at oracle-derived prices.
- LP funds are exposed to the full universe of traders rather than the curated set the admin intended.
- The pool admin has no on-chain recourse: they cannot allowlist the router for approved users while blocking it for others, because the extension has no per-user granularity at the router level.

This matches the "allowlist bypass" impact class: a disallowed user can still trade or deposit, causing direct LP loss or curation failure.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the canonical, documented periphery entry point for swaps. Any user who reads the periphery interface will naturally use the router. The bypass requires no special knowledge, no privileged access, and no unusual token behavior — only calling the public router with a valid swap path. The only precondition is that the pool admin has allowlisted the router (which is the only way to make router-mediated swaps work at all on an allowlisted pool).

---

### Recommendation

The extension must resolve the **original caller** rather than the pool-forwarded `sender`. Two approaches:

1. **Pass the original caller through the router.** The router stores the originating `msg.sender` in transient storage (already done for the payer context). Extend this to include the original swapper and encode it in `extensionData` so the extension can decode and check it. The extension would then verify the decoded original caller rather than the `sender` argument.

2. **Check `sender` against the router and then verify the original caller from `extensionData`.** The extension can require that when `sender` is a known router, the `extensionData` contains a signed or otherwise authenticated original caller identity.

The simplest correct fix is option 1: the router encodes `msg.sender` into `extensionData` before calling the pool, and `SwapAllowlistExtension.beforeSwap` decodes and checks that value when `sender` is a recognized router address.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, allowedUser, true)
  - Pool admin calls setAllowedToSwap(pool, routerAddress, true)
    (required so allowedUser can use the router)

Attack:
  - attacker (not in allowlist) calls:
      router.exactInputSingle({
        pool: pool,
        tokenIn: token0,
        tokenOut: token1,
        recipient: attacker,
        amountIn: X,
        ...
      })

  - router calls pool.swap(attacker, ...) with msg.sender = router
  - pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes successfully for the non-allowlisted attacker

Result:
  - attacker swaps against LP liquidity on a pool they were never permitted to access
  - allowlist is completely bypassed via the public router
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

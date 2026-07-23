The code confirms all parts of the claim. Let me verify the key facts:

1. `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` — confirmed at line 231. [1](#0-0) 

2. `ExtensionCalling._beforeSwap` forwards `sender` unchanged into the extension call — confirmed at lines 149–176. [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap` gates on `allowedSwapper[msg.sender][sender]` where `sender` is the direct pool caller — confirmed at line 37. [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with `params.extensionData` passed through unchanged — the router does **not** encode the originating user address into `extensionData`. So `msg.sender` of `pool.swap()` is the router, making `sender` = router address in the extension. [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap()` with the router as `msg.sender` and pass user-supplied `extensionData` without injecting user identity. [5](#0-4) 

The claim's code trace is fully confirmed by the production code.

---

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via Router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is designed to gate swaps by individual swapper identity per pool. However, `beforeSwap` checks `sender`, which is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router's address. If the pool admin allowlists the router to enable router-mediated swaps, every user of the router bypasses the per-user allowlist entirely, regardless of whether they were individually allowlisted.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` (line 231). `ExtensionCalling._beforeSwap` forwards that value unchanged into the encoded extension call (lines 149–176). `SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()` (line 37).

`MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` all call `pool.swap()` directly — the router is `msg.sender` of that call. None of these methods encode the originating user address into `extensionData`; they pass the caller-supplied `params.extensionData` through unchanged. As a result, `sender` arriving at the extension is always the router address, not the end user.

When a pool admin wants to allow router-mediated swaps for their allowlisted users, the natural action is `setAllowedToSwap(pool, router, true)`. This sets `allowedSwapper[pool][router] = true`, causing `beforeSwap` to pass for every router caller — including addresses that were never individually allowlisted — while the actual end-user identity is never checked.

Existing guards are insufficient: `allowAllSwappers` is a separate bypass toggle; the `onlyPoolAdmin` modifier on the setter is irrelevant to the check logic; and there is no mechanism in the extension or router to recover the real user address.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` for access-control purposes (e.g., KYC-gated liquidity, institutional-only pools) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users execute swaps against pool liquidity that was intended to be restricted, receiving output tokens from the pool. This is a direct loss-of-access-control impact: the pool's LP assets are exposed to counterparties the pool admin explicitly excluded.

## Likelihood Explanation
Medium. The bypass requires the pool admin to have allowlisted the router address — a natural and expected administrative action. Without it, no user can use the router against the pool at all. An admin who wants to support both direct and router-mediated swaps for their allowlisted users will allowlist the router, not realizing this opens the gate to all router users. After that, the trigger is fully unprivileged: any address calls the router.

## Recommendation
The `SwapAllowlistExtension` must gate on the actual end-user identity, not the direct pool caller. The router should encode the originating user address into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to supply the correct address. Alternatively, add explicit NatSpec warnings that allowlisting any intermediary contract grants access to all users of that contract, and that per-user gating only works for direct pool callers — but this does not fix the semantic gap.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  admin: setAllowedToSwap(pool, alice, true)       // alice is KYC'd
  admin: setAllowedToSwap(pool, router, true)       // enable router-mediated swaps

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle(pool=pool, ...)
    → router calls pool.swap(recipient=bob, ...)    // router is msg.sender
    → pool: _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓
    → swap proceeds, bob receives output tokens

Direct call check (bob, not allowlisted):
  bob calls pool.swap(...) directly
    → SwapAllowlistExtension.beforeSwap(sender=bob, ...)
    → allowedSwapper[pool][bob] == false  → revert NotAllowedToSwap ✓

Result: bob bypasses the allowlist via the router.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

### Title
`SwapAllowlistExtension` Gates on Router Address Instead of End-User, Allowing Any Caller to Bypass the Swap Allowlist - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router address (the only way to permit router-mediated swaps), every unprivileged user can bypass the curated allowlist by routing through the router.

---

### Finding Description

The pool's `swap` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `sender` (the pool's `msg.sender`): [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

At the pool, `msg.sender` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]` — the router's allowlist status — not the end user's. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The pool admin faces an inescapable dilemma:

| Admin choice | Consequence |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every non-allowlisted user bypasses the curated policy by routing through the router |

There is no configuration that simultaneously permits router-mediated swaps for allowlisted users and blocks non-allowlisted users.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd, institutional, or otherwise vetted participants loses that guarantee entirely once the router is allowlisted. Any unprivileged address can execute swaps against the pool's liquidity, draining LP assets at oracle-derived prices without the pool admin's consent. This is a direct loss of LP principal and a complete failure of the allowlist invariant.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, documented periphery entry point for end users. Any pool that wants to support router-mediated swaps must allowlist the router, making the bypass trivially reachable by any address. No special privilege, flash loan, or multi-step setup is required — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must recover the true end-user identity rather than the immediate pool caller. Two complementary approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData` before calling the pool. The extension decodes and verifies it. This requires a trusted router convention and is weaker if other callers omit the field.

2. **Check `sender` against a router registry and fall back to `extensionData`**: If `sender` is a known router, decode the real swapper from `extensionData`; otherwise treat `sender` as the swapper directly. This preserves backward compatibility for direct pool calls.

The stronger fix is approach 2 combined with a strict requirement that any registered router always encodes the originating user, enforced by the extension's own validation logic.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is KYC'd
  allowedSwapper[pool][router] = true  // admin must allowlist router for alice to use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
        pool: pool,
        recipient: bob,
        zeroForOne: true,
        amountIn: X,
        ...
    })

  router calls pool.swap(bob, true, X, ...)
    → pool: msg.sender = router
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → checks allowedSwapper[pool][router] == true  ✓ passes
    → swap executes, bob receives tokens

Result:
  bob bypassed the allowlist entirely.
  If router is NOT allowlisted, alice also cannot use the router — the admin has no valid configuration.
``` [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
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

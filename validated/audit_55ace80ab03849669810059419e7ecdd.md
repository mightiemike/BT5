### Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the actual user's address. Any pool that allowlists the router to support router-mediated swaps (the intended UX) simultaneously opens a bypass that lets every non-allowlisted user trade freely through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap()` is the **router**, not the user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

For any allowlisted pool to be usable through the router at all, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for **every** caller who routes through it, regardless of whether that caller is individually allowlisted.

The same bypass applies to `exactInput` and `exactOutputSingle` / `exactOutput` paths in the router. [5](#0-4) 

---

### Impact Explanation

Any non-allowlisted user can trade on a pool that is intended to be restricted (KYC-gated, institutional-only, compliance-restricted) by routing through `MetricOmmSimpleRouter`. The allowlist guard silently fails open for every router-mediated swap. LP funds in the restricted pool are exposed to unauthorized counterparties, and the pool's curation guarantee is entirely voided. This is a direct, unprivileged bypass of a core access-control extension with fund-impacting consequences (unauthorized parties can drain LP inventory at oracle prices).

---

### Likelihood Explanation

High. `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool operator who deploys a `SwapAllowlistExtension`-protected pool and wants to support the standard router UX must allowlist the router — at which point the bypass is immediately active for all users. No special privileges, no malicious setup, and no non-standard tokens are required. The attacker only needs to call the public router.

---

### Recommendation

The `SwapAllowlistExtension` must check the **actual user identity**, not the intermediary. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`, and the extension decodes and checks that address. This requires a coordinated change to the router and the extension.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is the user, but this breaks for multi-hop paths where intermediate recipients are the router itself.

The cleanest fix is approach 1: the router appends the original `msg.sender` to `extensionData`, and `SwapAllowlistExtension` decodes and verifies it, falling back to `sender` when no user identity is encoded (for direct pool calls).

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension in beforeSwap order.
2. Pool admin: setAllowedToSwap(pool, alice, true)
   — alice is the only intended swapper.
3. Pool admin: setAllowedToSwap(pool, router, true)
   — required so alice can use the router.
4. bob (not allowlisted) calls:
       router.exactInputSingle({
           pool:       pool,
           recipient:  bob,
           zeroForOne: true,
           amountIn:   X,
           ...
       })
5. Router calls pool.swap(bob, true, X, ...) with msg.sender = router.
6. Pool calls _beforeSwap(router, bob, ...).
7. Extension checks allowedSwapper[pool][router] → true.
8. bob's swap executes successfully despite not being allowlisted.
``` [3](#0-2) [1](#0-0) [6](#0-5)

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

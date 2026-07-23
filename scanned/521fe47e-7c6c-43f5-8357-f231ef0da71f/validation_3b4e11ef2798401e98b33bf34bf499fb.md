### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original Swapper, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through the public `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original user. Any pool admin who allowlists the router (a necessary step to let their curated users use the router) simultaneously opens the pool to every unprivileged user, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][sender]`, where `sender` is whatever address called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the pool see `msg.sender = router`: [4](#0-3) 

The router is a fully public, permissionless contract — any address can call it. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originalUser]`.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` from the router's address. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-only, institutional-only) and attaches `SwapAllowlistExtension` must allowlist the router address to let their approved users trade via the standard periphery. The moment the router is allowlisted, every unprivileged user can call `exactInputSingle` on that pool and the extension will pass — `allowedSwapper[pool][router] == true`. The allowlist is silently nullified for all router-mediated swaps. Disallowed users can drain the pool's liquidity at oracle prices, causing direct loss of LP assets and breaking the pool's curation invariant.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract with no access control.
- Any user who discovers the pool is allowlist-gated can trivially route through the router instead of calling the pool directly.
- The pool admin has no on-chain mechanism to allowlist the router for approved users while simultaneously blocking unapproved users from using the same router.
- No special preconditions are required beyond the pool admin having allowlisted the router (a routine and expected operational step).

---

### Recommendation

The `SwapAllowlistExtension` must gate the **original user**, not the immediate pool caller. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.

2. **Check `tx.origin` as a fallback** (not recommended for general use, but acceptable for allowlist-only extensions where the threat model is non-contract users).

3. **Preferred — store the original sender in transient storage**: The router writes `msg.sender` to a transient slot before calling the pool; the extension reads it. This is consistent with how the router already manages callback context via transient storage. [6](#0-5) 

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  3. Pool admin calls setAllowedToSwap(pool, router, true)  // allow router for alice
  4. bob (not KYC'd) is NOT in the allowlist.

Attack:
  5. bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool: curated_pool,
       recipient: bob,
       ...
     });

  6. Router calls pool.swap(bob_recipient, ...) — msg.sender = router.
  7. Pool calls _beforeSwap(sender=router, ...).
  8. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
  9. bob's swap executes at oracle price on the curated pool.

Result: bob bypasses the KYC allowlist and trades on a pool he should be barred from.
``` [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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

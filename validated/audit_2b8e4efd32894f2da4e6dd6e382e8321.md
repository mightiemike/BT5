### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. Any pool admin who allowlists the router to enable router-mediated swaps for their users inadvertently opens the pool to every user on the router, completely defeating the allowlist.

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`).
2. Router calls `IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, amount, ...)` — here `msg.sender` to the pool is the **router address**.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)` — passing the router as `sender`.
4. `ExtensionCalling._beforeSwap` encodes and forwards `sender = router` to the extension.
5. `SwapAllowlistExtension.beforeSwap(address sender, ...)` evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct key), but `sender` is the **router**, not the actual user. [1](#0-0) 

The pool passes `msg.sender` (the router) as the `sender` argument to the extension: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards it verbatim: [3](#0-2) 

The router calls `pool.swap()` directly for every hop (single and multi-hop): [4](#0-3) [5](#0-4) 

### Impact Explanation

A pool admin who wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)`. The moment the router is allowlisted, **every user** who calls any `exact*` function on the router passes the allowlist check, because the extension sees `sender = router` for all of them. The per-user allowlist is completely bypassed. Unauthorized users can trade on a pool that was intended to be restricted (e.g., institutional-only, KYC-gated, or counterparty-specific pools), causing direct loss of the curated access guarantee and potentially allowing adversarial actors to drain LP value at favorable oracle prices.

### Likelihood Explanation

Likelihood is **high**. The router is the standard, documented user-facing entry point for the protocol. Any pool admin who configures a `SwapAllowlistExtension` and also wants their allowlisted users to be able to use the router (the normal way to interact) will naturally allowlist the router address. The misconfiguration is not obvious because the admin believes they are enabling router access for their users, not for everyone. The bypass requires no special privileges — any user with a standard EOA can call the router.

### Recommendation

The `SwapAllowlistExtension` must gate by the **actual user**, not the intermediate caller. Two options:

1. **Pass the original user through the router**: The router should forward `msg.sender` (the actual user) as the `sender` argument to `pool.swap()`. The pool interface already accepts a `sender` parameter distinct from `msg.sender`; the router should populate it with the real user address rather than relying on the pool to use `msg.sender`.

2. **Check `recipient` instead of `sender`**: For swap allowlists, gate on the `recipient` field (which the router sets to the actual user's address), or add a separate `originator` field to the extension interface that the router explicitly populates.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, router, true)   // to enable router usage
  - Admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended allowlisted user
  - bob is NOT allowlisted

Attack:
  - bob calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(bob_recipient, ...) with msg.sender = router
  - pool calls _beforeSwap(router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - bob's swap succeeds despite not being allowlisted

Result:
  - bob trades on a restricted pool, bypassing the per-user allowlist
  - Any user can repeat this, making the allowlist entirely ineffective
```

### Citations

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

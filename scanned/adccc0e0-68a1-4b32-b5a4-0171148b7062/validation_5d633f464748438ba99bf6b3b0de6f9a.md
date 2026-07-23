### Title
SwapAllowlistExtension Gates Router Address Instead of Original User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks whether the **router** is allowlisted, not the original user. If the pool admin allowlists the router (the only way to let allowlisted users trade through the router), the allowlist is completely bypassed for every user.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool: [1](#0-0) 

The pool sets `sender` to its own `msg.sender` before calling the extension: [2](#0-1) 

`ExtensionCalling._beforeSwap` encodes that value as the first argument to every extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The result is a two-state trap for the pool admin:

| Admin action | Effect |
|---|---|
| Does **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlists the router** | Every user on the internet can bypass the allowlist via the router |

There is no configuration that simultaneously permits allowlisted users to trade through the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, institutional partners, or whitelisted market makers) loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and execute swaps against the pool. This constitutes a complete admin-boundary break: an unprivileged path bypasses a configured guard, violating the pool's intended access policy and potentially exposing LP assets to unauthorized counterparties.

---

### Likelihood Explanation

The bypass requires no special privileges, no flash loans, and no multi-transaction setup. Any user who can call the public router can exploit it. The only precondition is that the pool admin has allowlisted the router — a natural and expected action for any pool that intends to support the standard periphery flow. The likelihood is therefore high whenever a curated pool is deployed with the intention of supporting router-mediated trading.

---

### Recommendation

The extension must identify the **economic actor** (the end user), not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the original user through the router**: The router stores the original `msg.sender` in transient storage (it already does this for the payer in `_setNextCallbackContext`). The pool could expose this via a dedicated `sender` field in `extensionData`, and the extension could decode and check it. This requires a coordinated change across the router and extension.

2. **Check `recipient` instead of `sender`**: For swap allowlists the economically relevant actor is often the recipient of output tokens. If the pool admin allowlists recipients rather than callers, the router cannot forge a different recipient without the user's cooperation.

The simplest safe fix is to document that `SwapAllowlistExtension` is incompatible with the router and enforce this at the pool level, or redesign the extension to decode the true originator from `extensionData` supplied by a trusted router.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Deploy pool with ext as beforeSwap extension
address pool = factory.createPool(..., ext, ...);

// Admin allowlists alice and the router (so alice can use the router)
ext.setAllowedToSwap(pool, alice, true);
ext.setAllowedToSwap(pool, address(router), true); // ← required for alice to use router

// Bob (not allowlisted) calls the router directly
// router.msg.sender = bob, but pool.swap msg.sender = router
// extension checks allowedSwapper[pool][router] == true → passes
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1e18,
    ...
})); // ← succeeds; allowlist bypassed
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37, which checks `sender` (the direct pool caller) rather than the originating user: [6](#0-5)

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

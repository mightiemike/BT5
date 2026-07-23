### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Complete Allowlist Bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the original user. A pool admin who allowlists the router (required for any router-mediated swap to succeed) inadvertently opens the pool to every user, completely defeating the per-user allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`. The same substitution occurs for every router entry point — `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (including the recursive `_exactOutputIterateCallback` hops): [5](#0-4) [6](#0-5) 

For a curated pool to support router-mediated swaps for its allowlisted users, the pool admin must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, the check passes for **every** caller of the router, regardless of whether that caller is individually allowlisted. The per-user allowlist is completely bypassed.

This is the direct analog of the external report's storage-slot misassignment: the wrong key is used to look up the access-control mapping. Instead of the actual swapper's address, the intermediary's address is used, so the guard is applied to the wrong identity.

---

### Impact Explanation

A curated pool (e.g., KYC-only, institutional-only, or whitelist-gated) relies on `SwapAllowlistExtension` to enforce that only approved addresses can trade. Once the router is allowlisted, any unprivileged user can call `router.exactInputSingle()` and execute swaps on the restricted pool. This is a direct loss of the curation invariant and allows unauthorized users to extract value from LP positions that were priced for a controlled counterparty set.

---

### Likelihood Explanation

Pool admins who deploy a curated pool and also want their allowlisted users to access the standard router will naturally allowlist the router — there is no other way to make router-mediated swaps work. The bypass is therefore reachable on any curated pool that supports the periphery router, which is the expected production configuration.

---

### Recommendation

The extension must check the identity of the economic actor, not the intermediary. Two viable approaches:

1. **Pass the original user through the router**: The router could encode the original `msg.sender` in `extensionData` and the extension could decode and verify it (requires a trust model between router and extension).
2. **Check `recipient` instead of `sender` for swap allowlisting**: If the pool is designed so that the recipient is always the economic beneficiary, gating on `recipient` avoids the router-substitution problem. However, this changes the semantics of the allowlist.
3. **Require direct pool interaction for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and enforce this at the factory level by rejecting configurations that combine the two.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls:
     extension.setAllowedToSwap(pool, alice, true);       // allowlist alice
     extension.setAllowedToSwap(pool, router, true);      // required for router to work
3. bob (not allowlisted) calls:
     router.exactInputSingle({pool: pool, recipient: bob, ...})
4. Router calls pool.swap(bob, ...) with msg.sender = router.
5. Extension evaluates: allowedSwapper[pool][router] == true → passes.
6. bob's swap executes on the curated pool.
   Direct call by bob would revert: allowedSwapper[pool][bob] == false.
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at the `allowedSwapper[msg.sender][sender]` check, where `sender` is bound to the router address rather than the originating user whenever the periphery router is the direct caller of `pool.swap()`. [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```

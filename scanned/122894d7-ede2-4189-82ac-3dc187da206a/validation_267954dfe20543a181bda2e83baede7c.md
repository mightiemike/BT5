### Title
SwapAllowlistExtension Gates on Router Address Instead of End-User, Allowing Any User to Bypass Swap Curation via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end-user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for permitted users), every unpermissioned user can bypass the swap curation policy by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original user identity: [4](#0-3) 

So when a user routes through the router, the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → even permitted users cannot use the router.
- **Allowlist the router** → every user, permitted or not, can bypass the individual allowlist by routing through the router.

The same structural problem applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` as `msg.sender = router`: [5](#0-4) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to KYC'd or institutional counterparties is fully bypassed once the router is allowlisted. Any unpermissioned user can call `router.exactInputSingle()` and trade against LP funds that were intended to be protected. This is a direct loss of LP principal: the pool's spread and notional fees are collected by the protocol, but the LP's exposure to adverse selection from non-permitted counterparties is unlimited. The allowlist guard fails open for the entire router-mediated path, which is the primary user-facing entry point.

---

### Likelihood Explanation

Pool admins who configure a `SwapAllowlistExtension` and want their permitted users to access the standard router will naturally call `setAllowedToSwap(pool, router, true)`. This is the only way to make the router work for permitted users, so the misconfiguration is the expected operational path, not an edge case. The router is a public, permissionless contract, so once it is allowlisted, any address can exploit the bypass without any privileged access.

---

### Recommendation

The extension must gate on the **economic actor** (the end-user), not the intermediary. Two approaches:

1. **Pass the original user through the router**: Add a `swapFor(address user, ...)` pattern or include the original `msg.sender` in `extensionData` and have the extension decode it. The pool or router must cryptographically bind the user identity so it cannot be spoofed.

2. **Check `sender` only when it is a direct pool caller**: In `SwapAllowlistExtension.beforeSwap`, if `sender` is a known router, resolve the actual user from transient storage or a signed payload in `extensionData` before performing the allowlist lookup.

The simplest safe fix is to have `MetricOmmSimpleRouter` include the original `msg.sender` in a signed or authenticated field of `extensionData`, and have `SwapAllowlistExtension` decode and verify that field when `sender` is a recognized router.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as `beforeSwap` extension.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Pool admin calls `swapExtension.setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — `msg.sender` to pool = router.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes against LP funds, bypassing the curation policy entirely. [6](#0-5) [7](#0-6)

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

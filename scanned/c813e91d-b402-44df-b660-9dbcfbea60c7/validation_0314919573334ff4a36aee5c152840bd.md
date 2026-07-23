### Title
`SwapAllowlistExtension::beforeSwap` Checks Router Address Instead of Actual User — Any User Can Bypass Curated-Pool Swap Gate via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the actual user. If the pool admin allowlists the router (a natural step to enable router-mediated swaps for their curated users), every unprivileged user on the network can bypass the allowlist by routing through the same public router.

---

### Finding Description

**Call chain for a direct swap (allowlist works correctly):**
```
user → pool.swap()
  msg.sender = user
  _beforeSwap(sender=user, ...)
  SwapAllowlistExtension.beforeSwap(sender=user)
  → checks allowedSwapper[pool][user]   ✓ correct actor
```

**Call chain through `MetricOmmSimpleRouter` (allowlist checks wrong actor):**
```
user → MetricOmmSimpleRouter.exactInputSingle(params)
  router → pool.swap(params.recipient, ...)
    msg.sender = router
    _beforeSwap(sender=router, ...)
    SwapAllowlistExtension.beforeSwap(sender=router)
    → checks allowedSwapper[pool][router]  ✗ wrong actor
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` to the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with the router as `msg.sender`. [5](#0-4) 

**Bypass scenario:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict trading to a curated set of users.
2. Admin allowlists individual users: `setAllowedToSwap(pool, user1, true)`.
3. Admin also allowlists the router so that their curated users can trade via the standard periphery: `setAllowedToSwap(pool, router, true)`.
4. Any disallowed user calls `router.exactInputSingle({pool: pool, ...})`. The extension sees `sender = router`, which is allowlisted, and the swap proceeds — the actual caller is never checked.

---

### Impact Explanation

A curated pool's swap allowlist is completely bypassed for any user who routes through `MetricOmmSimpleRouter`. The pool admin's intent — restricting which counterparties can trade against LP positions — is silently defeated. Disallowed users can drain LP value through arbitrage or directional trading that the allowlist was designed to prevent. This is a direct loss of LP principal and owed fees above Sherlock thresholds on any pool where the router is allowlisted.

---

### Likelihood Explanation

The router is the canonical, documented periphery entry point for end-users. A pool admin who wants their curated users to be able to use the standard UI/router will naturally allowlist the router address. The admin has no indication from the extension's interface or documentation that doing so opens the gate to every user on the network. The trigger requires no privileged access — any EOA can call the public router.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the economically relevant actor, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass original user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and check it. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender`**: For swap allowlists, the recipient is often the actual beneficiary. However, this is also imperfect since `recipient` can be set to any address.

3. **Preferred — dedicated router-aware extension**: The extension should accept an optional "original sender" field in `extensionData`. If present, check that address; if absent (direct pool call), check `sender`. The router must always populate this field.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; router is allowlisted, attacker is not
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// attacker is NOT in allowlist

// Direct swap — correctly blocked:
vm.prank(attacker);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(attacker, false, 1000, type(uint128).max, "", "");

// Router-mediated swap — allowlist bypassed:
vm.prank(attacker);
// attacker approves tokenIn to router, then:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token1),
        recipient: attacker,
        deadline: block.timestamp + 1,
        amountIn: 1000,
        amountOutMinimum: 0,
        zeroForOne: false,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Swap succeeds — attacker traded on a curated pool without being allowlisted
```

The extension checks `allowedSwapper[pool][router]` (true), never inspecting the actual `attacker` address. [6](#0-5) [7](#0-6)

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

### Title
SwapAllowlistExtension Gates on Router Address Instead of Original User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` = `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original EOA. The allowlist check therefore gates on the router address. If the pool admin allowlists the router to enable router-mediated swaps for permitted users, every user — including explicitly disallowed ones — can bypass the allowlist by calling through the router.

---

### Finding Description

**Call chain when a user swaps via the router:**

```
User EOA
  → MetricOmmSimpleRouter.exactInputSingle(params)
      → IMetricOmmPoolActions(params.pool).swap(recipient, ...)
            // msg.sender to the pool = router address
          → MetricOmmPool._beforeSwap(msg.sender=router, recipient, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                    checks: allowedSwapper[pool][router]   ← wrong actor
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is whoever called the pool — the router when the user routes through periphery: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly with no mechanism to forward the original EOA: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap(...)` with the router as `msg.sender`. [5](#0-4) 

**The inescapable dilemma for the pool admin:**

| Admin configuration | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all — broken UX |
| Router **allowlisted** | Every user on-chain can bypass the allowlist via the router |

There is no configuration that simultaneously allows router-mediated swaps for permitted users and blocks disallowed users, because the extension has no visibility into the original EOA.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., institutional market makers, KYC'd addresses, or protocol-owned accounts) loses that restriction entirely once the router is allowlisted. Any unpermissioned user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool and execute swaps at the pool's oracle-derived bid/ask prices. In pools where the spread is calibrated for a specific counterparty set, unauthorized swaps extract LP value at prices the LPs never intended to offer to the general public. This is a direct loss of LP principal attributable to a broken core guard.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the protocol's primary user-facing swap interface. Any pool admin who wants allowlisted users to be able to use the standard router (rather than calling the pool directly) must allowlist the router address. This is a natural and expected operational step. Once taken, the bypass is unconditional and requires no special privileges — any EOA can exploit it.

---

### Recommendation

The `sender` argument passed to extensions must represent the economically relevant actor, not the intermediate contract. Two viable approaches:

1. **Router-forwarded origin**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it when present. This requires a convention between the router and the extension.

2. **Pool-level original-sender forwarding**: Add an optional `originalSender` parameter to `swap` that the router populates with `msg.sender` before calling the pool, and have the pool pass it as `sender` to extensions instead of `msg.sender`. The pool can default to `msg.sender` when the parameter is `address(0)`.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner explicitly passed by the caller), not on `sender` (the intermediate contract): [6](#0-5) 

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Pool admin allowlists alice and the router (to let alice use the router)
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attack: bob (not allowlisted) bypasses the guard via the router
vm.startPrank(bob);
token1.approve(address(router), type(uint256).max);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token1),
        recipient:       bob,
        deadline:        block.timestamp + 1,
        zeroForOne:      false,
        amountIn:        1000,
        amountOutMinimum: 0,
        priceLimitX64:   type(uint128).max,
        extensionData:   ""
    })
);
// ✓ swap succeeds — bob is not allowlisted but the router is
// The extension checked allowedSwapper[pool][router] == true, not allowedSwapper[pool][bob]
vm.stopPrank();
```

The pool sees `sender = router` (allowlisted), so `beforeSwap` passes. Bob receives tokens from a pool that was supposed to be closed to him, at prices the LP set for a restricted counterparty set.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

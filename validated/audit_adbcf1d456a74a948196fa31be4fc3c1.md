### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User, Allowing Any Unprivileged Caller to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the original user. The allowlist therefore gates the router address rather than the individual trader. Any user who is not on the allowlist can bypass the restriction by calling the router, and any pool admin who allowlists the router to support router-mediated swaps inadvertently opens the pool to all users.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received — the router address when the user entered through the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly without injecting the original user's address anywhere the extension can observe: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Contrast with the deposit allowlist**, which correctly checks `owner` (the position owner, not the liquidity adder contract), so it is not affected: [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional traders, or protocol-controlled accounts). The restriction is completely defeated for any user who routes through `MetricOmmSimpleRouter`:

- **If the pool admin allowlists the router** (the only way to make router-mediated swaps work for legitimate users): every unprivileged address can call `exactInputSingle` and trade against the pool's liquidity, because the extension sees `allowedSwapper[pool][router] == true` for all of them.
- **If the pool admin does not allowlist the router**: allowlisted users cannot use the router at all, breaking the supported periphery path.

In either case the allowlist invariant is broken. In the first case, unauthorized traders can drain LP value from a pool that was supposed to be restricted, constituting a direct loss of LP assets and a broken core pool functionality.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entrypoint documented in the protocol. Any user who is not on the allowlist can trivially call `exactInputSingle` on the router. No special privileges, flash loans, or multi-step setup are required. The bypass is reachable in a single transaction by any EOA or contract.

---

### Recommendation

The extension must gate the **original user**, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of `sender`. This requires a convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require the router to be trusted and forward user identity**: Add a router-aware path in the extension that reads the original user from a signed or transient context. This is more complex but avoids relying on `extensionData` conventions.

The simplest safe fix is option 1: the router encodes `abi.encode(msg.sender)` into `extensionData` for allowlisted pools, and the extension decodes and checks that address when `sender` is a known router.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Pool admin allowlists alice (a legitimate trader) but NOT bob.
swapExtension.setAllowedToSwap(address(pool), alice, true);

// Bob (not allowlisted) calls the router directly:
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bob,
    tokenIn: token0,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// The pool calls _beforeSwap(msg.sender=router, ...).
// SwapAllowlistExtension checks allowedSwapper[pool][router].
// If the admin allowlisted the router to support alice's router swaps,
// this check passes and bob's swap executes — allowlist bypassed.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

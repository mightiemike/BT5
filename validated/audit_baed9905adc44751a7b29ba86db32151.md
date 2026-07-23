### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real Swapper, Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router contract**, not the actual user. If the pool admin allowlists the router to support router-mediated swaps, every non-allowlisted user can bypass the curated-pool gate by calling the router. If the admin does not allowlist the router, every legitimately allowlisted user is silently blocked from using the supported periphery path.

---

### Finding Description

**Call chain — direct swap (correct):**
```
user → pool.swap()
  msg.sender = user
  _beforeSwap(sender=user, ...)
  SwapAllowlistExtension.beforeSwap(sender=user)
  → checks allowedSwapper[pool][user]   ✓ correct actor
```

**Call chain — router-mediated swap (broken):**
```
user → MetricOmmSimpleRouter.exactInputSingle(params)
  router → pool.swap(params.recipient, ...)
    msg.sender to pool = router
    _beforeSwap(sender=router, ...)
    SwapAllowlistExtension.beforeSwap(sender=router)
    → checks allowedSwapper[pool][router]  ✗ wrong actor
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `sender` (the router) rather than the real user: [3](#0-2) 

The router calls `pool.swap` with itself as `msg.sender` and the real user only as `recipient`: [4](#0-3) 

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

**Scenario A — Complete allowlist bypass (High):** A pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists the router so that their allowlisted users can swap through the standard periphery path (`allowedSwapper[pool][router] = true`). Because the extension checks the router address, every non-allowlisted user can now call `exactInputSingle` through the router and pass the guard. The curated-pool invariant is fully broken; unauthorized users trade against LP funds at oracle prices, causing direct LP loss on a pool that was designed to be restricted.

**Scenario B — Allowlisted users locked out (Medium/High):** If the admin does not allowlist the router, every user who is individually allowlisted (`allowedSwapper[pool][user] = true`) is silently blocked when they call any router function, because the extension sees the router address and reverts `NotAllowedToSwap`. The only usable path is a raw `pool.swap()` call, which requires the caller to implement `IMetricOmmSwapCallback` — an interface not available to EOAs. Core swap functionality is broken for the intended user set.

Both outcomes are contest-relevant: Scenario A is a direct allowlist bypass enabling unauthorized fund extraction from LP positions; Scenario B is broken core pool functionality causing an unusable swap flow.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entrypoint for end users. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router will encounter this bug on the first router-mediated swap. The trigger requires no special privileges — any public caller can reach it. The pool admin's only "safe" configuration (not allowlisting the router) locks out all allowlisted users from the router, making the extension effectively unusable with the periphery layer.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the **real economic actor**, not the intermediary. Two options:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` (the real user) into `extensionData` before calling `pool.swap`. The extension decodes and checks that address. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender` for router flows, or add a dedicated `originalSender` field to the extension interface:** The pool interface already carries both `sender` (the immediate caller) and `recipient`. For swap allowlists the economically relevant actor is the one initiating the trade, which the router should forward explicitly.

The cleanest fix is to have the router encode the real user address in `extensionData` and have `SwapAllowlistExtension` decode and verify it, with a fallback to `sender` when no override is present.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists the router so legitimate users can swap through it
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// Alice is NOT on the allowlist
// allowedSwapper[pool][alice] = false

// Alice calls the router — extension sees router as sender, passes the check
router.exactInputSingle(ExactInputSingleParams({
    pool:           address(pool),
    recipient:      alice,
    zeroForOne:     true,
    amountIn:       1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:  0,
    deadline:       block.timestamp,
    tokenIn:        token0,
    extensionData:  ""
}));
// ✓ swap succeeds — Alice bypassed the allowlist
// LP funds are traded against an unauthorized counterparty
```

The extension receives `sender = address(router)`, which is allowlisted, so `allowedSwapper[pool][router]` is `true` and the guard passes for Alice despite her not being on the allowlist. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

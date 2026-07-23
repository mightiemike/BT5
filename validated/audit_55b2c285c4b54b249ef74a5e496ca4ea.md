### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end-user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate swaps on curated pools by checking whether the swapper is on a per-pool allowlist. However, the `sender` argument it receives is always `msg.sender` inside `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` in the pool is the router contract, not the end-user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. Any unprivileged user can bypass the allowlist by routing through the public router.

---

### Finding Description

**Call chain for a direct swap (guard works correctly):**
```
User → pool.swap()
  msg.sender = User
  _beforeSwap(sender=User, ...)
  SwapAllowlistExtension.beforeSwap(sender=User)
  → checks allowedSwapper[pool][User]  ✓
```

**Call chain for a router-mediated swap (guard is misapplied):**
```
User → MetricOmmSimpleRouter.exactInputSingle()
  → pool.swap(recipient, ...)          // router is msg.sender to pool
      msg.sender = Router
      _beforeSwap(sender=Router, ...)
      SwapAllowlistExtension.beforeSwap(sender=Router)
      → checks allowedSwapper[pool][Router]  ✗ (wrong actor)
```

In `MetricOmmPool.swap()`, the pool unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address in the router path: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making itself `msg.sender` to the pool for every hop: [4](#0-3) 

The same applies to `exactInput` (all hops), `exactOutputSingle`, `exactOutput`, and the recursive `_exactOutputIterateCallback` — in every case the router is `msg.sender` to the pool. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants to support router-based swaps for their allowlisted users must add the router address to the allowlist. Once the router is allowlisted, **any** user — including those explicitly excluded — can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and the extension will pass because it sees the allowlisted router address as `sender`. The allowlist is completely defeated for all router-mediated swaps. This constitutes a broken core pool functionality (access-control bypass) with direct fund-impacting consequences: unauthorized parties can trade on pools that were designed to be restricted.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface documented and supported by the protocol. Any pool admin who enables the allowlist extension and also wants their allowlisted users to use the router will naturally add the router to the allowlist — this is the only way to make router swaps work at all. The bypass is therefore reachable through a routine, expected configuration. No special privileges, malicious setup, or non-standard tokens are required; any public user can call the router.

---

### Recommendation

The `sender` passed to the `beforeSwap` hook must represent the **economic actor** (the end-user), not the intermediary contract. Two complementary fixes:

1. **In the router**: pass the original `msg.sender` (the end-user) as the `sender` argument to `pool.swap()`. The pool's `swap` interface already accepts a `recipient` separately, so a dedicated `sender` parameter (or reuse of an existing field) could carry the true originator.

2. **In `SwapAllowlistExtension`**: if the pool cannot be changed, the extension can check the `recipient` field (which the router does set to the user-supplied address) — but this is fragile. The cleanest fix is at the pool/router boundary so that `sender` always reflects the true initiating user.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner), which is passed explicitly through the liquidity adder and is always the intended economic actor. [6](#0-5) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so that allowlisted users can use the router at all).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (not on allowlist) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: pool,
           recipient: attacker,
           ...
       })
  2. Router calls pool.swap(attacker, ...) — router is msg.sender to pool.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Attacker trades on a pool they were explicitly excluded from.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds.
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

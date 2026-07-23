### Title
`SwapAllowlistExtension` Gates on Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool, which is always `msg.sender` of the pool's `swap` call. When a user swaps through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the allowlist to every user on the network. Conversely, a pool admin who allowlists individual users finds that those users cannot swap through the router at all, breaking the primary swap interface.

---

### Finding Description

**Hook binding — what the pool passes**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

**What the extension checks**

`SwapAllowlistExtension.beforeSwap` gates on `sender` (the first parameter) keyed by `msg.sender` (the pool): [3](#0-2) 

**What the router actually passes**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly — the pool's `msg.sender` is therefore the **router**, not the end user: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**The inconsistency with the deposit allowlist**

`DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the actual position owner), not on `sender` (the liquidity adder): [6](#0-5) 

The swap allowlist has no equivalent "actual user" parameter — the pool's `swap` interface exposes only `recipient` (output destination) and `msg.sender` (immediate caller). The extension therefore cannot distinguish the real trader from the router.

---

### Impact Explanation

**Scenario A — Router allowlisted (allowlist fully bypassed):**
A pool admin configures `SwapAllowlistExtension` to gate a curated pool, then allowlists the router address so that users can reach the pool through the standard periphery interface. Because the extension checks `allowedSwapper[pool][router]`, every user on the network can now swap on the curated pool by routing through `MetricOmmSimpleRouter`. The per-user allowlist is completely inoperative. Non-allowlisted counterparties (e.g., non-KYC'd addresses on a regulated pool) can drain liquidity at oracle prices, and the LP's curation policy is silently voided.

**Scenario B — Individual users allowlisted (router-mediated swaps broken):**
A pool admin allowlists specific user addresses. Those users call `exactInputSingle` through the router. The extension receives `sender = router`, finds the router is not in `allowedSwapper`, and reverts with `NotAllowedToSwap`. Legitimate, allowlisted users cannot use the primary swap interface; the pool is effectively unusable through the router for its intended audience.

Both scenarios satisfy the allowed impact gate: Scenario A is an admin-boundary break where an unprivileged path bypasses an admin-configured guard; Scenario B is broken core swap functionality.

---

### Likelihood Explanation

- Pool admins deploying a curated pool will naturally allowlist the router to give their users access to the standard periphery — this is the expected operational pattern.
- The router is a public, permissionless contract; any address can call it.
- No special privilege, flash loan, or multi-transaction setup is required. A single `exactInputSingle` call from any address suffices once the router is allowlisted.
- The design inconsistency (deposit allowlist checks `owner`; swap allowlist checks `sender`) makes it easy for an admin to assume the swap allowlist behaves the same way.

---

### Recommendation

The `beforeSwap` hook signature should carry an explicit "actual swapper" field analogous to `owner` in `beforeAddLiquidity`. Until the interface is extended, the extension should document that allowlisting the router is equivalent to `allowAll = true`, and pool admins should be warned never to allowlist any public intermediary. A short-term mitigation is to add a check inside the extension that rejects calls where `sender` is a known router address and falls back to checking `extensionData` for a signed user identity, or to require direct pool interaction for allowlisted pools.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow router-mediated swaps while keeping the
     per-user allowlist active for direct callers.
3. Attacker (address NOT in allowedSwapper) calls:
     MetricOmmSimpleRouter.exactInputSingle({
       pool: curatedPool,
       ...
     })
4. Router calls pool.swap(...) — pool's msg.sender = router.
5. _beforeSwap(sender=router, ...) is dispatched.
6. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
7. Swap executes. Attacker receives output tokens.
8. The per-user allowlist was never consulted.
``` [7](#0-6) [8](#0-7) [9](#0-8)

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

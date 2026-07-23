### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass Swap Allowlist via Router - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual end-user. If the router is allowlisted so that legitimate users can reach the pool through it, every non-allowlisted user can bypass the curated pool's swap gate by routing through the same router.

---

### Finding Description

**Actor binding in the pool's `swap` call:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**The allowlist check in `SwapAllowlistExtension`:**

`SwapAllowlistExtension.beforeSwap` uses `sender` (first parameter) as the identity to check: [3](#0-2) 

**The router passes itself as `msg.sender` to the pool:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly — the pool sees `msg.sender == router`: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**The broken invariant:**

When a user calls the router, the extension receives `sender = router_address`. The pool admin faces an impossible choice:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | All router users blocked, even legitimate ones |
| Router **allowlisted** | Every user on-chain can bypass the allowlist via the router |

There is no way to allowlist the router for legitimate users while still blocking non-allowlisted users, because the extension never sees the actual end-user's address.

**Contrast with `DepositAllowlistExtension`:**

The deposit allowlist correctly checks `owner` (the position owner), which is a separate argument that the router cannot forge: [6](#0-5) 

The swap path has no equivalent "owner" argument — only `sender` (the caller) and `recipient` — so the wrong-actor binding has no in-protocol workaround.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to known counterparties (KYC, institutional, or partner-only pools) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The attacker receives real token output from the pool at oracle-derived prices; the pool's LP assets are consumed by an unauthorized party. This is a direct loss of LP principal and a complete failure of the pool's curation policy.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical, documented periphery entry point for swaps. Any user who discovers that the router is allowlisted on a curated pool can immediately exploit this with a standard router call. No privileged access, no special setup, and no non-standard token behavior is required. The trigger is a normal `exactInputSingle` call.

---

### Recommendation

Pass the actual end-user's address through the swap path so the extension can check it. Two approaches:

1. **Add a `payer` / `originator` field to the swap call** — the router sets it to `msg.sender` before calling the pool, and the pool forwards it to `beforeSwap` as a distinct argument alongside `sender`.
2. **Mirror the deposit pattern** — require callers to supply an explicit `swapper` address (analogous to `owner` in `addLiquidity`), and have `SwapAllowlistExtension` check that field instead of `sender`.

Either approach lets the allowlist key on the true economic actor rather than the intermediary contract.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin allowlists `alice` and the `MetricOmmSimpleRouter` address so that `alice` can trade through the router.
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle` with the curated pool as target.
4. The pool calls `_beforeSwap(msg.sender=router, ...)`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router] == true` → passes.
6. `bob` receives token output from the curated pool despite never being allowlisted.

The check that was supposed to block `bob` evaluated the router's address, not `bob`'s, because `SwapAllowlistExtension` receives `sender = router` and the router is allowlisted. [3](#0-2) [1](#0-0) [4](#0-3)

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

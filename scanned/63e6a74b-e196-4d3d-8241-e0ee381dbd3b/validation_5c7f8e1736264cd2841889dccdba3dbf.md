### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If a pool admin allowlists the router address (the natural step to enable router-mediated swaps for authorized users), every unprivileged user can bypass the allowlist by calling any `exact*` function on the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is in the per-pool allowlist, keyed by `msg.sender` (the pool): [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` to the pool: [3](#0-2) 

The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

The extension therefore sees `sender = router` for every router-mediated swap. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**Contrast with `DepositAllowlistExtension`**, which correctly checks the `owner` parameter (the position owner passed explicitly by the liquidity adder), not the direct caller: [5](#0-4) 

The two extensions are asymmetric: deposit gating is end-user-correct; swap gating is caller-correct only for direct pool calls.

---

### Impact Explanation

A pool admin who wants authorized users to trade via the router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, **any** address can call `router.exactInputSingle(pool, ...)` and the extension passes — the end user's identity is never checked. Unauthorized traders gain full swap access to a curated pool, can drain LP-owned liquidity at oracle-derived prices, and LP providers suffer direct principal loss. The invariant "a curated pool enforces the same allowlist policy regardless of which supported public entrypoint reaches it" is broken.

---

### Likelihood Explanation

The trigger is a pool admin allowlisting the router — a natural, expected operational step for any pool that wants to support the protocol's own periphery. The admin has no on-chain signal that doing so opens the pool to all users; the allowlist UI/admin flow gives no warning. The router is a public, permissionless contract reachable by anyone. No privileged attacker capability is required beyond calling a standard router function.

---

### Recommendation

Pass the original end-user identity through the swap path so the extension can gate it. Two options:

1. **Preferred — forward original caller in extensionData**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires no core changes.
2. **Alternative — add a `payer` / `originator` field to the pool's swap call**: The pool passes both `msg.sender` (direct caller) and an explicit originator to extensions, letting the extension choose which to gate.

Additionally, document clearly that allowlisting the router address grants swap access to all users, not just individually allowlisted ones.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin: allowedSwapper[pool][alice]   = true   // alice is authorized
  admin: allowedSwapper[pool][router]  = true   // router allowlisted so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (passes)
        → swap executes, bob receives output tokens

Result:
  bob bypasses the allowlist entirely.
  Repeatable for exactInput, exactOutputSingle, exactOutput.
  Any number of unauthorized users can drain the curated pool's liquidity.
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

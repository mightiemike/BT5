### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router**, not the actual user. If the pool admin allowlists the router (the only way to support router-mediated swaps on a restricted pool), every user on the network can bypass the allowlist by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the `sender` argument to every configured extension: [2](#0-1) 

**Step 2 — The extension checks `sender` against the allowlist.**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Step 3 — The router is the caller of `pool.swap()`, not the user.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly. The pool's `msg.sender` is the router contract: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**The resulting identity mismatch:**

| Entry path | `sender` seen by extension | Allowlist lookup |
|---|---|---|
| Direct `pool.swap()` | actual user | `allowedSwapper[pool][user]` ✓ |
| Via `MetricOmmSimpleRouter` | router address | `allowedSwapper[pool][router]` ✗ |

**The forced dilemma for the pool admin:**

A pool admin who wants to restrict swaps to specific counterparties must configure `SwapAllowlistExtension`. If they also want those counterparties to be able to use the router (the standard periphery path), they must call `setAllowedToSwap(pool, router, true)`. The moment they do, the allowlist check becomes `allowedSwapper[pool][router]` for every router-mediated call — meaning **any** user who routes through the router passes the check, regardless of whether they are on the allowlist. [6](#0-5) 

The `DepositAllowlistExtension` does not share this flaw: it checks `owner` (the position owner explicitly passed by the caller), not `sender`, so the liquidity adder path does not create an analogous bypass. [7](#0-6) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool — the admin intends to restrict trading to specific counterparties. Once the router is allowlisted (the only way to support the standard periphery path), the restriction is completely nullified: any unprivileged user can trade against the pool by routing through `MetricOmmSimpleRouter`. This is a direct admin-boundary break: the pool admin's configured access control is bypassed by an unprivileged public path. If the pool is designed for specific trading conditions (e.g., a private institutional pool), non-allowlisted users trading against it can cause LP losses through adverse selection or oracle-lag exploitation.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported periphery entry point for swaps. Any pool admin who wants allowlisted users to use the router must allowlist the router itself. This is a natural operational step, not an exotic configuration. The bypass is then reachable by any user with no special privileges, no front-running, and no multi-transaction setup.

---

### Recommendation

The extension must gate by the **economically relevant actor** — the user who initiated the trade — not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: For single-hop swaps where the user is also the recipient, `recipient` identifies the actual user. This does not generalize to multi-hop paths where intermediate recipients are the router itself.

3. **Separate router-level allowlist**: Deploy a router wrapper that enforces the allowlist before forwarding to the pool, and restrict direct `pool.swap()` calls to that wrapper only (requires pool-level caller restriction, which is not currently supported).

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  admin calls: setAllowedToSwap(pool, alice, true)       // alice is allowlisted
  admin calls: setAllowedToSwap(pool, router, true)      // router allowlisted for periphery use

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
    })

Execution trace:
  1. router.exactInputSingle() → pool.swap(recipient=bob, ...) [msg.sender = router]
  2. pool._beforeSwap(sender=router, ...)
  3. extension.beforeSwap(sender=router, ...)
  4. check: allowedSwapper[pool][router] == true  → PASSES
  5. swap executes; bob receives output tokens

Result: bob, who is not on the allowlist, successfully swaps on the restricted pool.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-25)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
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

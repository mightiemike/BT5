### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router**, not the user. The allowlist therefore gates the router address, not the actual economic actor. Any user can bypass a curated pool's per-user allowlist by routing through the router once the pool admin allowlists the router to restore normal router-based swap functionality.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The allowlist is therefore keyed on `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Two broken states arise from this mismatch:**

1. **Broken functionality (immediate):** A pool admin allowlists specific users (e.g., KYC addresses). Those users call `exactInputSingle`. The extension checks `allowedSwapper[pool][router]`; the router is not on the list; the swap reverts. Allowlisted users cannot use the standard periphery path at all.

2. **Allowlist bypass (after admin remediation):** To restore router-based swaps, the admin adds the router to the allowlist. Now `allowedSwapper[pool][router] = true`, so every call through the router passes the guard regardless of who the actual user is. Any address — including addresses the admin explicitly never allowlisted — can swap by routing through `MetricOmmSimpleRouter`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` and checks the `owner` parameter, which callers supply explicitly: [6](#0-5) 

The pool's `addLiquidity` accepts `owner` as a caller-supplied argument, so the liquidity adder can pass the real user. The swap path has no equivalent: `pool.swap()` has no `swapper` parameter — only `recipient` — so the only identity the pool can forward is `msg.sender` (the router). [7](#0-6) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC, institutional, or protocol-internal actors) loses that restriction entirely once the router is allowlisted. Any unprivileged address can trade against the pool's liquidity, violating the curation invariant and potentially exposing LP funds to actors the pool admin explicitly intended to exclude. This matches the "allowlist bypass through a public router path" impact class.

---

### Likelihood Explanation

The broken-functionality state (Scenario 1) is reached immediately by any allowlisted user who tries the standard periphery path. The admin's natural remediation — allowlisting the router — directly produces the bypass (Scenario 2). No adversarial setup is required beyond using the publicly deployed router. The router is the documented and expected swap interface, so the probability that a pool admin encounters and "fixes" this is high.

---

### Recommendation

The `SwapAllowlistExtension` must check the actual economic actor, not the intermediary. Two complementary fixes:

1. **Add a `swapper` parameter to `pool.swap()`** (analogous to `owner` in `addLiquidity`) so the pool can forward the real user identity to extensions. The router would pass `msg.sender` (the user) as `swapper`.

2. **Until the pool interface is changed**, document that `SwapAllowlistExtension` is incompatible with router-based flows and must only be used with direct pool calls, or allowlist the router only when per-user gating is not required.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension as beforeSwap hook
  admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  admin calls setAllowedToSwap(pool, router, true)  // admin adds router to fix alice's broken flow

Attack:
  bob (not KYC'd) calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes for bob despite bob never being allowlisted

Result:
  allowedSwapper[pool][bob] == false  (never set)
  but bob successfully swaps against the curated pool
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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

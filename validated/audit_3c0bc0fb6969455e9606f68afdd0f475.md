### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the actual end user. If the pool admin allowlists the router (the natural step to enable router-based swaps for allowlisted users), every unprivileged user can bypass the per-user restriction by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension dispatcher.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` encodes that `sender` and forwards it to every configured extension.** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`.**

`msg.sender` here is the pool (correct); `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call.** [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Result:** The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual end user is never checked.

**Contrast with `DepositAllowlistExtension`:** The deposit guard correctly checks `owner` (the position owner), not `sender` (the payer), because the pool passes both separately. The swap path has no equivalent second identity field — only `sender` (= `msg.sender` of `swap`) is available to the extension. [5](#0-4) 

---

### Impact Explanation

Two broken outcomes arise from the same root cause:

| Scenario | Effect |
|---|---|
| Pool admin does **not** allowlist the router | Allowlisted users cannot swap through the router; the standard periphery path is unusable for curated pools |
| Pool admin **does** allowlist the router (to unblock allowlisted users) | `allowedSwapper[pool][router] = true`, so **any** address can bypass the per-user restriction by routing through `MetricOmmSimpleRouter` |

In the second scenario — which is the natural operational choice — the swap allowlist is completely defeated. Any non-allowlisted user can execute swaps against a curated pool, exposing LP funds to unauthorized counterparties and violating the pool admin's intended access policy. This qualifies as broken core pool functionality with direct LP exposure.

---

### Likelihood Explanation

The pool admin who deploys a `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses. The `MetricOmmSimpleRouter` is the canonical swap entry point documented and shipped with the protocol. A pool admin who wants allowlisted users to be able to use the router will naturally add the router to the allowlist — a single, reasonable operational step that silently opens the gate to everyone. The mistake is non-obvious because the admin sees "router is allowed" and expects only router users to benefit, not realizing the router is a shared public contract.

---

### Recommendation

The extension must gate the **economic actor** (the end user), not the **transport layer** (the router). Two viable approaches:

1. **`extensionData` identity forwarding:** The router encodes the originating user's address into `extensionData`; the extension decodes and checks it. This requires the router to commit the user identity and the extension to trust the pool's forwarding of `extensionData` (already done faithfully).

2. **Separate `swapper` parameter in the hook interface:** Extend `IMetricOmmExtensions.beforeSwap` with an explicit `swapper` field distinct from `sender`, populated by the pool from a router-supplied argument (e.g., a dedicated field in `callbackData` or a new swap parameter).

Until fixed, pools that need per-user swap gating must not use `SwapAllowlistExtension` together with `MetricOmmSimpleRouter`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)      // alice is the intended swapper
  pool admin calls setAllowedToSwap(pool, router, true)     // to let alice use the router

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
      pool:      pool,
      recipient: bob,
      ...
    })

  Router executes:
    pool.swap(bob, ...)   // msg.sender = router

  Pool calls:
    _beforeSwap(router, ...)

  Extension evaluates:
    allowedSwapper[pool][router] == true  ✓  → swap proceeds

  Result: bob swaps successfully against the curated pool
          despite never being added to the allowlist.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

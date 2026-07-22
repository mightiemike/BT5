### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. The extension therefore checks whether the **router** is allowlisted, not the actual trader. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the pool to every user, because any non-allowlisted address can bypass the per-user gate simply by calling the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the pool's `msg.sender` the router contract, not the end user: [4](#0-3) 

The same pattern applies to `exactInput` (every hop) and `exactOutput` (every recursive callback hop): [5](#0-4) 

The result is a structural catch-22:

- If the pool admin does **not** allowlist the router, every allowlisted user is silently blocked from using the router (broken core functionality).
- If the pool admin **does** allowlist the router (the natural fix to restore router access for their curated users), the check becomes `allowedSwapper[pool][router] == true` for every caller, and any non-allowlisted address can bypass the gate by routing through the router.

---

### Impact Explanation

A curated pool's swap allowlist is its primary access-control boundary. Once the router is allowlisted, any address — including addresses the pool admin explicitly excluded — can execute swaps against the pool's liquidity. This exposes LP assets to unauthorized trading, violates the pool's curation invariant, and can result in direct loss of LP principal through adverse-selection trades that the allowlist was designed to prevent.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical user-facing entry point documented and deployed alongside the core. Pool admins who configure a `SwapAllowlistExtension` and also want their allowlisted users to use the router will naturally allowlist the router address. The flaw is silent: the admin receives no error and the pool appears to function normally. Any non-allowlisted user who discovers the router is allowlisted can immediately exploit the bypass with a standard router call.

---

### Recommendation

The extension must gate the **original user**, not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the hook.** Add an `originator` field to the `beforeSwap` hook arguments (or use `extensionData`) so the router can forward `msg.sender` (the end user) explicitly. The extension then checks `allowedSwapper[pool][originator]`.

2. **Short-term mitigation.** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and that allowlisting the router negates per-user gating. Pool admins must call `pool.swap()` directly or deploy a wrapper that enforces identity before calling the pool.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // alice is the intended curated user
  allowedSwapper[pool][router] = true   // admin adds this to let alice use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: bob})

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=bob, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, bob receives tokens

Result:
  bob, who is not in the allowlist, successfully swaps against the curated pool.
  The allowlist invariant is broken; LP assets are exposed to unauthorized trading.
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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

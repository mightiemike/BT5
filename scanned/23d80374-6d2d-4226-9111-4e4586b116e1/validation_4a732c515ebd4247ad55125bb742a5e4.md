### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (a natural step to let their allowlisted users use the router), every non-allowlisted user can bypass the per-user gate by routing through the same router.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the pool see `msg.sender = router`. The original end-user address is never forwarded to the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Dual-mechanism analog to the external report:** The allowlist has two independent bypass paths — `allowAllSwappers[pool]` (global flag) and `allowedSwapper[pool][router]` (individual entry for the router). A pool admin who wants their allowlisted users to be able to use the router must add the router to `allowedSwapper`. Once the router is in the allowlist, the per-user gate is silently open to every caller of the router, mirroring the dual-mechanism auth bypass in the external report.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, protocol-internal actors, or whitelisted market makers) is fully bypassed by any unprivileged user who calls `MetricOmmSimpleRouter` on that pool, provided the router is allowlisted. The attacker receives oracle-priced output tokens from a pool that was supposed to be closed to them. This constitutes a direct curation failure and, depending on pool design, can result in loss of LP principal or protocol fees if the pool's pricing assumptions depend on the restricted participant set.

**Severity: High** — direct policy bypass on curated pools; any user can trigger it permissionlessly once the router is allowlisted.

---

### Likelihood Explanation

A pool admin who deploys a curated pool and wants their allowlisted users to access it via the standard periphery router will naturally add the router to `allowedSwapper`. This is the only way to let allowlisted users use the router, so the misconfiguration is an expected operational step, not an exotic edge case. The bypass requires no special privileges, no flash loans, and no multi-transaction setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the economic actor (the end user), not the intermediate contract. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should accept an optional `swapper` override in its params struct and pass it through `extensionData` or a dedicated field so extensions can recover the real user identity.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode the real swapper from `extensionData` when the immediate `sender` is a known router, or the pool interface should be extended to carry the originating EOA alongside `msg.sender`.

Until fixed, pool admins must not add the router to `allowedSwapper` on curated pools; doing so silently opens the pool to all users.

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with SwapAllowlistExtension
  admin sets allowedSwapper[pool][alice]   = true   // alice is KYC'd
  admin sets allowedSwapper[pool][router]  = true   // to let alice use the router
  bob is NOT in allowedSwapper

Attack (single tx, no special privileges):
  bob calls router.exactInputSingle({
      pool:          pool,
      recipient:     bob,
      zeroForOne:    true,
      amountIn:      X,
      extensionData: ""
  })

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=bob, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
            → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                allowedSwapper[pool][router] == true  ← check passes
        → swap executes, bob receives oracle-priced token1
        → router callback pays token0 from bob

Result: bob, a non-allowlisted user, successfully swaps on a curated pool.
        The allowlist guard is silently bypassed through the router.
``` [6](#0-5) [7](#0-6)

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

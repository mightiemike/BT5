### Title
`SwapAllowlistExtension` Gates on Router Address Instead of Original User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router (a natural action to support the standard periphery), every unprivileged user can bypass the per-user allowlist by calling the router.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
    extensionData
);
``` [1](#0-0) 

**Step 2 — The router calls `pool.swap()` without forwarding the original user.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly. The router is `msg.sender` to the pool; the original user's address is never forwarded:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // user-controlled, not the user's identity
);
``` [2](#0-1) 

**Step 3 — The extension checks the wrong actor.**

`SwapAllowlistExtension.beforeSwap` receives `sender = router` and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The lookup is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. [3](#0-2) 

The allowlist is keyed `mapping(address pool => mapping(address swapper => bool))`, so the pool admin must allowlist the router address, not individual users, for router-mediated swaps to work at all. [4](#0-3) 

---

### Impact Explanation

Two concrete failure modes, both fund-impacting:

| Scenario | Outcome |
|---|---|
| Pool admin allowlists individual users but **not** the router | Allowlisted users cannot use the standard periphery router at all — core swap functionality is broken for the intended user set |
| Pool admin allowlists the router to support periphery usage | **Every** unprivileged user can bypass the per-user allowlist by calling `router.exactInputSingle()` — the curated pool is fully open |

The second scenario is the direct loss path: a pool deployed specifically to restrict swaps to KYC'd or protocol-approved addresses is rendered open to any caller. Swaps drain LP reserves at oracle prices, so the loss is bounded only by pool liquidity.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps.
- A pool admin who configures `SwapAllowlistExtension` and also wants users to use the router will naturally allowlist the router address — the admin has no on-chain signal that this opens the pool to everyone.
- The bypass requires no special privilege: any EOA calls `router.exactInputSingle()` with the target pool.
- The `exactInput` multi-hop path has the same flaw for every hop. [5](#0-4) 

---

### Recommendation

The extension must gate on the **economically relevant actor** — the original user — not the intermediary contract. Two viable approaches:

1. **Pass original caller through `extensionData`**: The router encodes `msg.sender` into a dedicated slot of `extensionData` before calling the pool; the extension decodes and verifies it. This requires a convention between router and extension.

2. **Dedicated `originalSender` field in the hook interface**: Add an `originalSender` parameter to `beforeSwap` that the pool populates from a transient-storage context set by the router before calling `pool.swap()`. This is the cleanest separation.

Until fixed, pool admins must **not** allowlist the router address on pools that use `SwapAllowlistExtension` with per-user restrictions.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted to support periphery

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})
      → pool.swap(msg.sender=router, ...)
        → _beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓ passes
          → swap executes for bob

Result:
  bob swaps on a pool that was supposed to block him.
  The allowlist invariant is broken; any user can swap by routing through the router.
```

The `exactOutput` and multi-hop `exactInput` paths in the router are equally affected, as all of them call `pool.swap()` with `msg.sender = router`. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

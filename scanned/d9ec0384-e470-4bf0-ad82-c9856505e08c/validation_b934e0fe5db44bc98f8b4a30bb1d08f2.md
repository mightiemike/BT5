### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which the pool sets to `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently opens the pool to **all users**, completely defeating the allowlist.

---

### Finding Description

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as the `sender` argument to the extension: [1](#0-0) 

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the economic actor
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the first parameter) is allowlisted for the calling pool (`msg.sender` of the extension call = the pool): [2](#0-1) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()`, the pool's `msg.sender` is the **router**, not the end user: [3](#0-2) 

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for the pool admin:

| Admin configuration | Direct swap | Router swap |
|---|---|---|
| Allowlist specific users only | ✅ Correct | ❌ Allowlisted users blocked (DoS) |
| Allowlist the router | ❌ All users bypass | ✅ Works |
| Allowlist users + router | ❌ All users bypass | ✅ Works |

There is **no configuration** that correctly restricts router-mediated swaps to only allowlisted users.

---

### Impact Explanation

If the pool admin allowlists the router address (the natural step to enable router-mediated swaps for legitimate users), the `SwapAllowlistExtension` check becomes `allowedSwapper[pool][router] == true` for every user who calls the router. Any unprivileged user can then swap on a curated/restricted pool by routing through `MetricOmmSimpleRouter`, bypassing the intended per-user access control entirely. This is a direct loss of the curation guarantee the pool admin configured, and on pools where the allowlist is the primary protection against adversarial flow (e.g., oracle-sensitive pools), it exposes LP funds to unauthorized swap activity.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical periphery swap path. Pool admins who deploy a `SwapAllowlistExtension`-gated pool and want to support standard user tooling will naturally add the router to the allowlist. The misconfiguration is not obvious from the extension's interface (`setAllowedToSwap(pool, router, true)` looks like "allow router users"), and there is no warning in the extension or router code. Likelihood is **Medium**.

---

### Recommendation

The `SwapAllowlistExtension` must gate by the **economic actor**, not the direct pool caller. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted-forwarder convention between the router and extension.

2. **Check `recipient` instead of `sender`**: For swap allowlists, the recipient is often the economic beneficiary and is user-controlled. However, this changes the semantics and may not fit all use cases.

3. **Document the incompatibility**: If the design intent is that `sender` = direct pool caller, document explicitly that `SwapAllowlistExtension` is incompatible with router-mediated swaps and must only be used with direct pool calls.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow router-mediated swaps for legitimate users.
3. Attacker (not individually allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
4. Router calls pool.swap(...); pool's msg.sender = router.
5. Extension evaluates: allowedSwapper[pool][router] == true → passes.
6. Attacker's swap executes on the restricted pool.
7. Any user can repeat step 3 — the allowlist is fully bypassed.
``` [2](#0-1) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L228-241)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

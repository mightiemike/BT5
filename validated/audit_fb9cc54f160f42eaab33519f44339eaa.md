### Title
`SwapAllowlistExtension` gates the router address instead of the end user, enabling complete allowlist bypass through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router**, not the end user. The allowlist therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This is the direct analog of the DYAD H-06 bug: the guard checks the wrong identity (router vs. user) just as DYAD checked the wrong collateral type (non-Kerosene vs. Kerosene).

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

`sender` is populated by `ExtensionCalling._beforeSwap`, which passes `msg.sender` of the pool's `swap()` call:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- direct caller of pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user routes through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
``` [3](#0-2) 

So `msg.sender` at the pool is the **router contract**, not the end user. The extension receives `sender = router` and evaluates `allowedSwapper[pool][router]`.

**Two broken scenarios result:**

**Scenario A — Allowlist bypass:** If the pool admin allowlists the router address (a natural action to enable router-based swaps), every user — including those not individually allowlisted — can bypass the per-user gate by routing through `MetricOmmSimpleRouter`.

**Scenario B — Allowlisted users blocked:** If the admin allowlists specific user addresses but not the router, those users cannot use the supported periphery path at all. They must call `pool.swap()` directly, which breaks the intended UX and the protocol's own router integration.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in the router, and to the recursive `_exactOutputIterateCallback` path where intermediate hops call `pool.swap(msg.sender=router, ...)`. [4](#0-3) 

---

### Impact Explanation

**Scenario A (bypass):** A non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle` on a curated pool. The router is allowlisted. The extension passes. The user executes a swap that the pool admin explicitly intended to block. This is a complete failure of the swap allowlist guard with direct fund-flow consequences: the pool's curated LP positions are exposed to unrestricted trading.

**Scenario B (blocked):** Allowlisted users cannot use the protocol's own router, breaking core swap functionality for the intended participants.

Both impacts are contest-relevant: Scenario A is a direct allowlist bypass (admin-boundary break); Scenario B is broken core pool functionality.

---

### Likelihood Explanation

The trigger is unprivileged: any user can call `MetricOmmSimpleRouter`. Scenario A requires the pool admin to allowlist the router, which is a natural and expected configuration step for any pool that intends to support router-based swaps. Scenario B occurs by default whenever the admin allowlists individual users without also allowlisting the router. Both paths are reachable on every production pool that deploys `SwapAllowlistExtension`.

---

### Recommendation

The `beforeSwap` hook must gate the **economically relevant actor** — the end user — not the intermediary router. Two approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.

2. **Check `recipient` instead of `sender` when `sender` is a known router:** Not robust; routers are not enumerable.

3. **Preferred — mirror the deposit allowlist pattern:** `DepositAllowlistExtension` correctly checks `owner` (the position owner, explicitly passed by the caller), not `sender`. For swaps, the router should pass the originating user as a dedicated field, and the extension should check that field. Alternatively, the pool admin documentation must explicitly warn that allowlisting the router grants access to all users. [5](#0-4) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to enable router-based swaps for the pool)
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)
    (alice is not individually allowlisted)

Attack:
  1. alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=alice, ...)
     → pool's msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap(sender=router, ...)
     checks: allowedSwapper[pool][router] == true  ✓ passes
  5. alice's swap executes successfully

Expected: revert NotAllowedToSwap (alice is not allowlisted)
Actual:   swap succeeds (router is allowlisted, alice bypasses the gate)
``` [6](#0-5) [7](#0-6) [3](#0-2)

### Citations

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

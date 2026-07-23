### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which the pool sets to `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap` is the router contract, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged user can bypass the individual allowlist by calling the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap, not the end-user
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
``` [1](#0-0) 

**Step 2 — `SwapAllowlistExtension` checks that `sender` argument against the allowlist.**

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is whatever the pool forwarded — the router address.

**Step 3 — The router calls `pool.swap` without forwarding the actual user.**

```solidity
// MetricOmmSimpleRouter.sol L71-80
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
``` [3](#0-2) 

The actual end-user (`msg.sender` of `exactInputSingle`) is stored only in transient storage for the payment callback; it is never forwarded to `pool.swap`. The pool therefore passes the router address as `sender` to the extension.

**The bypass chain:**

| Who calls whom | `msg.sender` seen by pool | `sender` seen by extension |
|---|---|---|
| User → pool directly | user | user ✓ |
| User → router → pool | router | router ✗ |

A pool admin who wants legitimate users to swap via the router must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the extension passes for **every** caller of the router, regardless of whether that caller is individually allowlisted.

The same wrong-actor binding applies to `exactInput` (all hops call `pool.swap` as the router) and to intermediate hops of `exactOutput` (the callback calls the next pool as the previous pool, so the allowlist would need to allowlist the previous pool). [4](#0-3) 

---

### Impact Explanation

Any user who is **not** individually allowlisted can swap on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`/`exactOutputSingle`/`exactOutput`), provided the router is allowlisted. The pool's LP funds are consumed by unauthorized swappers, violating the access-control invariant the pool admin configured. This is a direct loss of LP assets and a broken core swap flow on allowlisted pools.

---

### Likelihood Explanation

A pool admin who deploys a curated pool and wants legitimate allowlisted users to be able to use the standard router **must** allowlist the router — there is no other mechanism to enable router-mediated swaps. This is the natural, expected configuration for any allowlisted pool that integrates with the periphery. The bypass is therefore reachable in every realistic allowlisted-pool deployment that supports the router.

---

### Recommendation

The extension must gate the **economic actor** (the end-user), not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. The extension must also verify that the encoding came from a trusted router (e.g., via a factory-registered router registry).

2. **Add a `swapper` field to the pool's swap interface**: Extend `pool.swap` with an explicit `swapper` parameter that the pool forwards to extensions, and have the router populate it with `msg.sender`. This keeps the extension logic simple and trustless.

Until fixed, pool admins should **not** allowlist the router address; instead, require users to call `pool.swap` directly.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)      // alice is the intended user
3. Pool admin calls setAllowedToSwap(pool, router, true)     // needed so alice can use the router
4. bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — msg.sender = router
6. Pool calls extension.beforeSwap(router, ...) — sender = router
7. Extension checks allowedSwapper[pool][router] → true
8. bob's swap executes successfully, bypassing the allowlist.
```

The allowlist check in `SwapAllowlistExtension.beforeSwap` at line 37 passes because `sender` is the router, not `bob`. The pool admin's intent to restrict swaps to `alice` is silently defeated. [5](#0-4) [6](#0-5) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```

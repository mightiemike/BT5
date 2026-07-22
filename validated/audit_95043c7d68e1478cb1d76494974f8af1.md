### Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the actual end-user, allowing complete allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict which addresses may swap in a pool. Its `beforeSwap` hook checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user bypasses the per-user allowlist entirely. If the admin instead allowlists individual users, those users cannot use the router at all, breaking the primary user-facing entry point.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the first argument to `_beforeSwap` is `msg.sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` argument to every configured extension: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` gates on that `sender`.**

```solidity
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

**Step 3 — The router is `msg.sender` of `pool.swap()`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly; the pool therefore sees `msg.sender = router`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Resulting invariant break — two mutually exclusive failure modes:**

| Admin intent | What admin configures | Actual outcome |
|---|---|---|
| Allow only specific users | `allowedSwapper[pool][alice] = true` | Alice cannot use the router (router not allowlisted → revert). Core UX broken. |
| Allow router-mediated swaps | `allowedSwapper[pool][router] = true` | **Every user** can swap through the router. Per-user allowlist is completely bypassed. |

There is no configuration that simultaneously (a) allows router-mediated swaps and (b) enforces per-user restrictions, because the extension cannot distinguish which end-user is behind the router call.

---

### Impact Explanation

**Allowlist bypass (high-impact path):** When the pool admin allowlists the router to support the standard periphery flow, any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and swap in a pool that was intended to be restricted. Restricted pools may hold concentrated liquidity at oracle-anchored prices specifically for a curated set of counterparties; opening them to arbitrary swappers exposes LP principal to uninvited adverse selection and drains owed LP assets.

**Core functionality break (DoS path):** When the admin allowlists individual users instead, those users cannot use the router at all. The primary user-facing entry point is rendered unusable for any allowlisted pool, breaking the withdraw/swap flow.

Both outcomes satisfy the allowed impact gate: the bypass is an admin-boundary break (unprivileged path circumvents a factory/pool role check), and the DoS breaks core pool swap functionality.

---

### Likelihood Explanation

`SwapAllowlistExtension` is a production periphery extension. Any pool that deploys it and also expects users to interact via `MetricOmmSimpleRouter` (the standard entry point) is immediately affected. No special attacker capability is required — a normal `exactInputSingle` call suffices. Likelihood is **Medium**: the extension must be configured and the router must be in use, but both are the expected production setup.

---

### Recommendation

The extension must gate on the economically relevant actor, not the intermediate dispatcher. Two options:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. This requires a coordinated convention between router and extension.

2. **Check `recipient` instead of (or in addition to) `sender`:** For single-hop swaps the recipient is often the actual user. This is imprecise for multi-hop paths where intermediate recipients are the router itself.

3. **Dedicated router allowlist entry:** Document that pools using `SwapAllowlistExtension` must not allowlist the router; users must call the pool directly. This is the least invasive fix but breaks the standard UX.

The cleanest fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and the extension decodes the true initiator from that field when `sender` is a known router.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured in beforeSwap slot.
2. Admin calls setAllowedToSwap(pool, router, true)  // enable router-mediated swaps
3. Attacker (not allowlisted) calls:
       router.exactInputSingle({
           pool: pool,
           recipient: attacker,
           zeroForOne: true,
           amountIn: X,
           ...
       })
4. Pool calls extension.beforeSwap(router, attacker, ...)
5. Extension checks allowedSwapper[pool][router] == true  → passes
6. Swap executes. Attacker receives tokens from a restricted pool.
   Per-user allowlist is completely bypassed.
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

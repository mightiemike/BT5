### Title
SwapAllowlistExtension Checks Router Address Instead of End-User Address, Allowing Any User to Bypass Per-User Swap Allowlists via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the `msg.sender` of the pool's `swap` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the end user's address. If the pool admin allowlists the router (a natural action to enable router-mediated swaps), every user — including non-allowlisted ones — can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Root cause — wrong actor checked:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension caller) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is the `msg.sender` of the pool's own `swap` call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- this is the pool's msg.sender, i.e. the router
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
``` [2](#0-1) 

**Router call path — end-user identity is lost:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The pool's `msg.sender` is therefore the router contract, not the end user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
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

The end user's address (`msg.sender` of the router call) is stored only in transient callback context for payment settlement — it is never forwarded to the pool or to any extension. The extension therefore sees `sender = router address` for every user who goes through the router.

**The bypass:**

| Scenario | `sender` seen by extension | Allowlist check | Result |
|---|---|---|---|
| Non-allowlisted user calls pool directly | user address | `allowedSwapper[pool][user]` → false | Correctly blocked |
| Non-allowlisted user calls via router | router address | `allowedSwapper[pool][router]` → **true** (if router is allowlisted) | **Bypass succeeds** |

The admin is forced into an impossible choice:
- **Do not allowlist the router** → allowlisted users cannot use the router at all.
- **Allowlist the router** → every user, including non-allowlisted ones, can bypass the per-user gate.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

Any user who is not on the allowlist can swap on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`, provided the pool admin has allowlisted the router address. The pool admin's intended access-control boundary (e.g., KYC/AML gating, institutional-only pools, whitelist-only liquidity programs) is silently nullified. This is an admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses a factory/pool admin-configured guard.

---

### Likelihood Explanation

The router is the primary user-facing entry point for swaps in the protocol. A pool admin who wants to support router-mediated swaps for their allowlisted users must allowlist the router — there is no other mechanism. This is a natural, expected administrative action. Once the router is allowlisted, the bypass is unconditional and requires no special privileges or timing from the attacker.

---

### Recommendation

The `sender` parameter forwarded to extensions must represent the **economic actor** (the end user), not the immediate caller of `pool.swap`. Two approaches:

1. **Router forwards end-user address via `extensionData`**: The router encodes `msg.sender` into `extensionData` and the extension decodes it. This requires a convention between router and extension.
2. **Pool exposes an authenticated sender field**: The pool could accept an explicit `swapper` argument (separate from `msg.sender`) that the router populates with the end user's address, and extensions receive this field instead of `sender`.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the position recipient, explicitly provided by the caller) rather than `sender`. A similar approach — checking an explicitly provided identity rather than the immediate caller — would fix the swap allowlist.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allowlist Alice.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The pool calls `_beforeSwap(msg.sender=router, ...)`.
6. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully, bypassing the per-user allowlist.

Direct pool call by Bob (without router) would correctly revert with `NotAllowedToSwap` because `allowedSwapper[pool][bob]` is `false`. The bypass is exclusive to the router path. [4](#0-3) [5](#0-4) [6](#0-5)

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

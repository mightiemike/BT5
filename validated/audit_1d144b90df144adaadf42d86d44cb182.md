Audit Report

## Title
Swap Allowlist Bypass via Router — Any User Can Bypass `SwapAllowlistExtension` by Routing Through `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `pool.swap()`. When any swap is routed through `MetricOmmSimpleRouter`, the router is always the direct caller of `pool.swap()`, so `sender` resolves to the router address rather than the originating user. Any pool admin who allowlists the router to support router-mediated swaps inadvertently opens the pool to all users, completely defeating the per-user allowlist.

## Finding Description

**Root cause in `SwapAllowlistExtension.beforeSwap`:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension caller) and `sender` is the first argument forwarded by the pool.

**What the pool forwards as `sender`:**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`: [2](#0-1) 

So `sender` = `msg.sender` of `pool.swap()`. When the router calls the pool, `msg.sender` of the pool is the router, not the original user.

**Router always calls `pool.swap()` directly:**

For `exactInputSingle`, the router calls `pool.swap()` directly: [3](#0-2) 

For multi-hop `exactInput`, every hop is called by the router: [4](#0-3) 

For `exactOutput`, intermediate hops are called from within the callback, still with the router as `msg.sender`: [5](#0-4) 

In every router path, the extension receives `sender = router_address`.

**The dilemma for pool admins:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all (DoS on a supported periphery path) |
| Allowlist the router | **All** users can bypass the per-user allowlist by routing through the router |

Once `allowedSwapper[pool][router] = true`, any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle()` and the extension passes because it sees the router, not the user. [6](#0-5) 

## Impact Explanation

The swap allowlist — a core pool access-control mechanism — is completely defeated for any user who routes through `MetricOmmSimpleRouter`. The invariant "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it" is broken. Disallowed users can trade on restricted pools, constituting unauthorized access to private liquidity pools and a regulatory/compliance failure. This is broken core pool functionality with direct curation failure, matching the allowed impact gate.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported swap entrypoint for end users. Any pool admin who deploys a curated pool and also wants users to be able to use the router (the normal UX path) will allowlist the router, triggering the bypass. The trigger requires no special privileges — any user can call the router. The condition is a natural and expected admin configuration, making exploitation highly likely in practice.

## Recommendation

The `SwapAllowlistExtension` must gate the **original user**, not the intermediary router. Two viable approaches:

1. **Pass original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trust assumption that the router is the only allowed intermediary.
2. **Trusted router registry**: The extension maintains a registry of trusted routers; when `sender` is a trusted router, it reads the original user from `extensionData` and checks that address instead.
3. **Document incompatibility**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory/config validation layer.

## Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin allowlists user1:
       swapExt.setAllowedToSwap(pool, user1, true)
3. Admin allowlists the router to support router-mediated swaps:
       swapExt.setAllowedToSwap(pool, router, true)
4. user2 (NOT allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap() → msg.sender of pool = router.
6. Pool calls _beforeSwap(router, ...) → extension receives sender = router.
7. Extension checks allowedSwapper[pool][router] → true → passes.
8. user2 successfully swaps on the curated pool, bypassing the allowlist.
```

The wrong actor (`router`) is checked instead of the original user because the pool unconditionally forwards `msg.sender` as `sender` to the extension. [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```

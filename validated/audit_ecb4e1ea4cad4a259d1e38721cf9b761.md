Audit Report

## Title
SwapAllowlistExtension Evaluates Router Address as Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` parameter, which `MetricOmmPool.swap` sets to its own `msg.sender`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. If the pool admin allowlists the router to enable router-based swaps for legitimate users, every unpermissioned user gains unrestricted access to the curated pool through the router.

## Finding Description

**Call chain confirmed in production code:**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap() — the router when routed
    recipient,
    ...
    extensionData
);
```

`ExtensionCalling._beforeSwap` encodes this value verbatim as the first positional argument of `IMetricOmmExtensions.beforeSwap`:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
```

`SwapAllowlistExtension.beforeSwap` checks that exact `sender` value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original caller:

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
```

The original `msg.sender` is stored only in transient callback context (for payment purposes) and is never forwarded to the pool or extension. The pool therefore passes the router address as `sender` to the extension. The extension evaluates `allowedSwapper[pool][router]`, completely ignoring the actual user.

**Two broken outcomes:**

| Admin action | Result |
|---|---|
| Router NOT added to allowlist | All router-based swaps revert with `NotAllowedToSwap`, even for allowlisted users |
| Router added to allowlist (to enable router usage) | Every user, including those explicitly excluded, can swap freely through the router |

The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` without forwarding the original caller.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd, institutional, or otherwise vetted counterparties loses that guarantee entirely once the router is allowlisted. Any unpermissioned user can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's liquidity. LP funds are exposed to counterparties the pool admin explicitly intended to exclude. This constitutes a direct bypass of the access-control protection the pool was configured to enforce — an admin-boundary break reachable by any unprivileged EOA through the primary supported periphery path.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported periphery path for swaps. A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist — there is no other mechanism. The moment they do so, the bypass is open to everyone. The trigger requires no special privileges, no flash loans, and no unusual token behavior; any EOA can call the router. The precondition (router allowlisted) is the natural and expected operational state for any pool that intends to support router-based trading.

## Recommendation

The `sender` parameter passed to `beforeSwap` must represent the economic actor, not the intermediary. Two complementary fixes:

1. **Thread the end user through the router:** `MetricOmmSimpleRouter` should accept and forward the original `msg.sender` as an explicit `sender` argument to `pool.swap`, and the pool interface should accept a caller-supplied sender (with trust constraints, e.g., only from factory-registered routers).

2. **Alternatively, add a direct-call guard in the extension:** `SwapAllowlistExtension.beforeSwap` should revert if `sender` is a known router or if `sender != msg.sender`'s caller — though since the extension only sees the pool as `msg.sender`, the cleanest architectural fix is option 1.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is added so Alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, recipient, ...)` → extension receives `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps against the curated pool despite being explicitly excluded from the allowlist.

The same path is reproducible via `exactInput`, `exactOutputSingle`, and `exactOutput`.
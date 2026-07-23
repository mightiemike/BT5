All three code references check out against the actual production code:

- `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no mechanism to forward the original caller [2](#0-1) 
- `SwapAllowlistExtension.beforeSwap` checks only `allowedSwapper[msg.sender][sender]` where `sender` is the router address when routed [3](#0-2) 

---

Audit Report

## Title
`SwapAllowlistExtension` allowlist fully bypassed via `MetricOmmSimpleRouter` when router is allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` sets to its own `msg.sender` — the direct caller of the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every unprivileged user can bypass the per-user allowlist by calling the router, breaking the pool's access-control invariant entirely.

## Finding Description
**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`. When the user goes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract address, not the end user.

**Step 2 — Router calls `pool.swap()` with no user-identity forwarding.**
`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly. The original `msg.sender` (the end user) is stored only in transient callback context for payment purposes and is never forwarded to the pool as the swap initiator. The pool therefore sees `msg.sender = address(router)`.

**Step 3 — Extension checks the router address, not the user.**
`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the argument passed by the pool — i.e., the router address. There is no fallback to decode `extensionData` for a user identity field.

**Step 4 — The bypass.**
For any allowlisted user to swap through the router, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the check at line 37 passes for every caller of the router — including addresses that were never individually allowlisted. The extension has no mechanism to distinguish which end user initiated the router call.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) is fully bypassed. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the restricted pool without authorization. The pool's core access-control invariant — that only allowlisted addresses may trade — is broken, constituting broken core pool functionality and potential unauthorized drain of LP assets at oracle-derived prices.

## Likelihood Explanation
The bypass requires the pool admin to allowlist the router. This is a natural and expected operational step: allowlisted users who want to use the router for slippage protection, multi-hop routing, or deadline enforcement cannot do so unless the router is allowlisted, because the extension will reject the router's address. The pool admin has no way to allowlist the router for specific users only — it is an all-or-nothing grant. Any pool that combines `SwapAllowlistExtension` with `MetricOmmSimpleRouter` and enables router access for its users is fully exposed.

## Recommendation
The `sender` argument forwarded to extensions should represent the economically relevant actor (the end user), not the intermediary contract. The simplest safe fix is to have the router append `abi.encode(msg.sender)` to `extensionData` before calling `pool.swap()`, and have `SwapAllowlistExtension.beforeSwap` decode and check that value when the direct caller (`sender`) is a known/registered router. Alternatively, the pool could expose a trusted-router registry so the extension can fall back to the decoded user identity from `extensionData` when `sender` is a registered router.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
  alice wants to use the router → pool admin calls setAllowedToSwap(pool, router, true)

Attack:
  charlie (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
      pool: restrictedPool,
      recipient: charlie,
      zeroForOne: true,
      amountIn: X,
      ...
    })

  Execution trace:
    router.exactInputSingle()                              // msg.sender = charlie
      → pool.swap(recipient=charlie, ...)                 // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (passes)
        → swap executes, charlie receives tokens

charlie successfully swaps against the restricted pool despite never being individually
allowlisted, because the router address satisfies the allowlist check.
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

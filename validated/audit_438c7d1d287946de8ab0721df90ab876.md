Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender` — the router contract when a user routes through `MetricOmmSimpleRouter`. If the router is allowlisted (the only way to support router-mediated swaps), every unpermissioned user can bypass the per-user allowlist by calling the router, nullifying the curated access control entirely.

## Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original EOA. The original `msg.sender` is stored only in transient storage for the payment callback — it is never passed to the pool: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The pool always sees `msg.sender` = router, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. There is no authenticated mechanism in `extensionData` to convey the real caller identity — `params.extensionData` is fully user-controlled and unauthenticated.

## Impact Explanation

A curated pool using `SwapAllowlistExtension` is designed to restrict which addresses may trade against its liquidity. Allowlisting the router (the only way to support normal UX) opens the gate to every caller of the router. Unpermissioned users can execute swaps at oracle-anchored prices against LP positions intended for a controlled set of counterparties, constituting a direct loss of LP principal and a broken core pool invariant (curated access control).

## Likelihood Explanation

The router is the primary production entry point. Any pool that wants to support normal UX must allowlist the router. This makes the bypass reachable on every curated pool that uses `SwapAllowlistExtension` with router support enabled. No privileged setup beyond the natural pool configuration is required; any unpermissioned EOA can trigger it immediately.

## Recommendation

The extension must gate on the economic actor, not the intermediary:

1. **Pass the original user through the router.** `MetricOmmSimpleRouter` should forward `msg.sender` as an authenticated field inside `extensionData` (signed or verified via a trusted router registry), and `SwapAllowlistExtension` should decode and check that field instead of the raw `sender` argument.
2. **Alternatively, maintain a trusted router registry in the extension.** When `sender` is a known router, require the real user identity to be supplied and verified via a signed payload in `extensionData`. When `sender` is not a router, check `sender` directly as today.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — the natural step to enable router-mediated swaps.
3. Pool admin calls `setAllowedToSwap(pool, userA, true)` — intending to restrict swaps to `userA` only.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
6. Pool calls `_beforeSwap(router, recipient, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` = `true` → no revert.
8. `userB`'s swap executes successfully against the curated pool's liquidity, bypassing the intended per-user gate.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

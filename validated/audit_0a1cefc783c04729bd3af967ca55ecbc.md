Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist on Curated Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Any pool admin who allowlists the router to permit router-mediated swaps inadvertently opens the pool to every user on the network, completely voiding the curated-pool restriction.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` of that call: [3](#0-2) 

The same pattern holds for `exactInput` (every hop): [4](#0-3) 

And for `exactOutputSingle` and `exactOutput`, which also call `pool.swap()` directly with the router as `msg.sender`. The result is that the extension evaluates `allowedSwapper[pool][router]` for every router-mediated swap, regardless of which end user initiated the transaction. The existing `allowAllSwappers` escape hatch does not help — it only bypasses the check entirely, it does not fix the identity confusion.

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` must allowlist the router to permit any router-mediated swap (the standard user-facing path). Once `allowedSwapper[pool][router] = true`, the check passes for **every** caller of the router, including addresses the admin explicitly never allowlisted. This is a direct admin-boundary break: the pool admin's intent to restrict trading to a specific set of counterparties is silently voided for all router users. Any non-allowlisted address can bypass the restriction by calling `MetricOmmSimpleRouter.exactInputSingle` instead of `pool.swap` directly.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool using `SwapAllowlistExtension` that needs to support router-mediated swaps — the normal case — must allowlist the router, triggering the bypass automatically. No special privileges, flash loans, or unusual token behavior are required; a standard `exactInputSingle` call suffices. The bypass is repeatable and unconditional once the router is allowlisted.

## Recommendation

Pass the originating user rather than the immediate caller as the `sender` argument to extensions. One concrete approach: the router already stores the real user in transient storage via `_setNextCallbackContext` (payer field); the pool can read it back to supply as `sender` to extensions. Alternatively, `SwapAllowlistExtension` can be redesigned to accept an explicit originator field passed through `extensionData`, but this requires all callers to cooperate. The cleanest fix is for the pool to propagate the true originator through the hook arguments.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary to allow any router-mediated swap.
3. Non-allowlisted address `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `attacker` successfully swaps on a pool that was supposed to block them.

The existing unit test `test_blocksSwapWhenSwapperNotAllowed` calls `_swap` which goes directly to the pool, not through the router, and therefore does not catch this bypass path.

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

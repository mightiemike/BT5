Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of originating user, enabling allowlist bypass or breaking router access for curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap` forwards its own `msg.sender` as the `sender` argument to every configured extension. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This breaks the allowlist invariant in both directions: if the router is allowlisted, every user bypasses the curated pool's access control; if it is not, every allowlisted user is blocked from using the protocol's primary swap interface.

## Finding Description
`MetricOmmPool.swap` unconditionally passes `msg.sender` — the direct caller of `pool.swap()` — as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  L230-240
_beforeSwap(
    msg.sender,   // always the direct caller of pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then gates on that value:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The router stores the original `msg.sender` only in transient callback context for payment purposes; it is never surfaced to the pool or extensions. The same pattern applies to `exactInput`, `exactOutputSingle`, `exactOutput`, and the recursive `_exactOutputIterateCallback` (L220-228), where `pool.swap(msg.sender=router, ...)` is called again.

The effective check is therefore `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. No existing guard corrects this: the extension has no access to the original initiating address, and the pool provides no alternative channel for it.

## Impact Explanation
**Scenario A — router allowlisted (bypass):** A pool admin who adds the router to the allowlist as a "trusted intermediary" inadvertently opens the pool to every EOA. Any address can call `router.exactInputSingle()` and the extension passes because `allowedSwapper[pool][router] == true`. The curated pool's access control is fully defeated — an unprivileged trader swaps against a pool they are not permitted to access.

**Scenario B — router not allowlisted (broken functionality):** A legitimately allowlisted user who calls `router.exactInputSingle()` receives `NotAllowedToSwap` because `allowedSwapper[pool][router] == false`, even though `allowedSwapper[pool][user] == true`. The router — the protocol's documented primary swap interface — is completely unusable for any curated pool, constituting broken core swap functionality.

Both outcomes represent a direct, fund-impacting or functionality-breaking failure of the allowlist invariant.

## Likelihood Explanation
Every swap routed through `MetricOmmSimpleRouter` against a pool that has `SwapAllowlistExtension` in its `BEFORE_SWAP_ORDER` triggers this mismatch. The router is the primary public entrypoint for swaps. Any pool that deploys this extension and expects users to interact via the router is immediately affected. No special attacker capability is required beyond calling a public router function.

## Recommendation
The pool must surface the original initiating address to extensions. The preferred fix is a pool-level change: before calling `pool.swap()`, the router writes the original `msg.sender` into a dedicated transient storage slot; `_beforeSwap` reads that slot and passes it as an additional `originalSender` field to extension calls. `SwapAllowlistExtension.beforeSwap` then checks `originalSender` instead of (or in addition to) `sender`. This mirrors the existing transient-storage pattern already used by the router for callback context (`_setNextCallbackContext`).

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Call `setAllowedToSwap(pool, router, true)` — allowlisting the router (Scenario A), or leave the router un-allowlisted (Scenario B).
3. **Scenario A:** Call `router.exactInputSingle(...)` from any EOA not individually in the allowlist. The swap succeeds because `allowedSwapper[pool][router] == true`. The wrong value evaluated is `allowedSwapper[pool][router]` where `allowedSwapper[pool][user]` is the invariant-correct check.
4. **Scenario B:** Call `router.exactInputSingle(...)` from an EOA that IS individually allowlisted. The swap reverts with `NotAllowedToSwap` because `allowedSwapper[pool][router] == false`, even though `allowedSwapper[pool][user] == true`. The wrong value evaluated is again `allowedSwapper[pool][router]`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

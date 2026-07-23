Audit Report

## Title
`SwapAllowlistExtension.beforeSwap()` checks the router address instead of the actual user, allowing any caller to bypass the per-pool swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the router contract becomes `msg.sender` of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to permit their approved users to swap via the standard UX inadvertently grants every network participant the ability to bypass the allowlist entirely.

## Finding Description
`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value verbatim as the first positional argument of the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router contract the `msg.sender` of that call: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap()` with the router as `msg.sender`: [5](#0-4) [6](#0-5) 

The actual user's address (`msg.sender` of the router call) is stored only in transient callback context for payment purposes and is never forwarded to the pool or extension. The extension's allowlist check is therefore structurally blind to the true economic actor for all router-mediated swaps.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a named set of addresses. To allow those addresses to use the canonical router, the admin must add the router to the allowlist. Once the router is allowlisted, `allowedSwapper[pool][router]` evaluates to `true` for every caller of the router, including addresses the admin never approved. The allowlist guard is completely neutralised for router-mediated swaps. Any user can execute swaps against the curated pool at oracle prices, bypassing the curation policy. This constitutes a broken core pool access-control mechanism causing direct loss of the pool's intended swap restriction and potential fund exposure to unauthorized counterparties — matching the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds" impact criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical periphery swap entry point. Pool admins who deploy curated pools with `SwapAllowlistExtension` will routinely allowlist the router so their permitted users can swap normally. The bypass requires no special privilege, no flash loan, and no multi-step setup — any address calls `exactInputSingle` (or any other `exact*` function) on the router pointing at the curated pool. The condition is self-inflicted by the expected admin workflow and is therefore highly likely to be triggered in production.

## Recommendation
The extension must gate the economically relevant actor, not the intermediary. Two complementary approaches:

1. **Pass the original user through the router.** Add a `swapper` field to `ExactInputSingleParams` (and equivalent structs) that defaults to `msg.sender`. Encode it into `extensionData` and have `SwapAllowlistExtension.beforeSwap()` decode and check it when present, falling back to `sender` for direct pool calls.

2. **Expose an `originalCaller` field at the pool level.** Have `MetricOmmPool.swap()` accept and forward a separate `originalCaller` argument (analogous to ERC-1271's signer vs. forwarder distinction) so every extension can gate the true economic actor regardless of the call path.

Option 1 is deployable without core pool changes. Option 2 is the cleanest long-term fix.

## Proof of Concept
```
Setup
─────
1. Deploy MetricOmmPool with SwapAllowlistExtension (beforeSwap order = 1).
2. Pool admin calls setAllowedToSwap(pool, ALICE, true).
   ALICE is the only permitted swapper.
3. Pool admin calls setAllowedToSwap(pool, address(router), true)
   so ALICE can use the router.

Attack
──────
4. BOB (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: BOB, ...})
5. Router calls pool.swap(BOB, ...) — msg.sender of pool.swap = router.
6. Pool calls extension.beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] → true → passes.
8. BOB's swap executes at oracle price; BOB receives token output.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist bypassed.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

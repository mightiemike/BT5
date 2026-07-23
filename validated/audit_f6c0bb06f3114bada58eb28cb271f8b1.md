Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Original User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than the **original user**. Any pool admin who allowlists the router to enable permitted users to trade through it simultaneously grants every unpermissioned user the ability to bypass the allowlist by routing through the same public contract.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool via `_callExtensionsInOrder`) and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension via `abi.encodeCall`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The pool's `msg.sender` is the **router address**, so the extension receives `sender = router`. The original user's address (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment purposes and is never forwarded to the extension. The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops via `_exactOutputIterateCallback`).

This creates an inescapable dilemma: if the admin does not allowlist the router, permitted users cannot use the router at all. If the admin allowlists the router, every unpermissioned user can bypass the allowlist by routing through the same public contract.

## Impact Explanation

A pool deploying `SwapAllowlistExtension` is a curated pool where only specific addresses may trade (e.g., KYC'd counterparties or protocol-controlled addresses). Once the pool admin allowlists the router to restore normal UX for permitted users, any unpermissioned address can call `exactInputSingle` through the router and the extension's check passes because `allowedSwapper[pool][router] == true`. The allowlist is completely nullified — unauthorized users can swap on the curated pool, draining liquidity at oracle prices and bypassing the curation policy entirely. This constitutes a **High** severity direct loss of user principal / broken core pool functionality (the allowlist is the pool's primary access control mechanism).

## Likelihood Explanation

The router is the standard, documented periphery entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router (the expected UX) must allowlist the router. The bypass requires no special privileges, flash loans, or multi-block setup — a single `exactInputSingle` call from any EOA suffices. It is reachable on any production curated pool that has not deliberately blocked all router access.

## Recommendation

The extension must gate the **original user**, not the intermediary. The most robust fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode and check it when `sender` is a known router. Alternatively, the pool interface can be extended to carry the originating user address as a dedicated field separate from the callback `msg.sender`. As a minimal safe documentation fix, the extension must explicitly document that the router must never be allowlisted and that allowlisted users must call the pool directly — but this is a severe UX restriction that breaks the intended periphery integration.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is permitted
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router allowlisted so alice can use it

Attack (by bob, who is NOT allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curated_pool,
         recipient: bob,
         ...
     })
  2. Router calls pool.swap(...) — pool's msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. ExtensionCalling forwards sender=router to SwapAllowlistExtension.beforeSwap
  5. Extension checks allowedSwapper[pool][router] == true → passes
  6. Swap executes; bob receives tokens from the curated pool

Result: bob, a non-allowlisted user, successfully swaps on a curated pool.
        The allowlist is completely bypassed.
```

Verified code references: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

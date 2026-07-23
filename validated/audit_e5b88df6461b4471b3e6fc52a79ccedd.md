All cited code references are confirmed against the actual repository. The vulnerability is real and exploitable:

- `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap` [1](#0-0) 
- `_beforeSwap` forwards `sender` directly to the extension [2](#0-1) 
- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the router, not the end user [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` without forwarding the original caller's identity [4](#0-3) 
- `DepositAllowlistExtension` correctly gates by `owner` (the economically relevant actor), not `sender` [5](#0-4) 

---

Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Curated-Pool Allowlist — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` from the pool's perspective — the router, not the end user. When a pool admin allowlists the router to permit their KYC'd users to trade via the standard UI, every unprivileged user gains the same access by calling the router, completely defeating the curated-pool access control.

## Finding Description
In `MetricOmmPool.swap`, `msg.sender` (the router) is passed as `sender` to `_beforeSwap`, which forwards it unchanged to `SwapAllowlistExtension.beforeSwap`. The extension checks `allowedSwapper[pool][sender]` where `sender` is the router address. A pool admin who wants allowlisted users to trade via `MetricOmmSimpleRouter` must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, any EOA can call `router.exactInputSingle(...)` and the extension sees `sender = router` (allowlisted), granting the swap regardless of who the actual caller is. The router stores `msg.sender` only in transient storage for the payment callback context and never passes it to the pool as the initiator identity. `DepositAllowlistExtension` avoids this by gating on `owner` (the LP's identity, passed separately), but no equivalent correct binding exists for swaps.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` to restrict trading to specific addresses (KYC'd counterparties, whitelisted market makers) loses that protection entirely for any user routing through `MetricOmmSimpleRouter`. Non-allowlisted users execute swaps at oracle-anchored prices, extracting arbitrage or draining LP value that the allowlist was designed to prevent. This is a direct loss of LP principal and a complete curation failure — a High severity impact under Sherlock thresholds.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap entrypoint. Any pool admin who allowlists the router (the natural and necessary step to let their allowlisted users use the standard UI) simultaneously opens the pool to all users. No special privilege is required: any EOA can call `exactInputSingle` or `exactInput`. The bypass is reachable on every router-mediated swap against a pool with `SwapAllowlistExtension` configured and the router allowlisted.

## Recommendation
The extension must gate by the original user, not the intermediary. The cleanest fix is for the router to store `msg.sender` in transient storage and expose it via a `getInitiator()` view, and for the pool to pass this value as a separate `initiator` field to extensions alongside `sender`. `SwapAllowlistExtension.beforeSwap` should then check `initiator` when `sender` is a known periphery contract. Alternatively, document `SwapAllowlistExtension` as incompatible with the router and enforce this at the pool configuration level by reverting if the router is allowlisted.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)
    → intended to let allowlisted users reach the pool via the router
  pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  attacker (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: attacker, ...})

  router calls:
    pool.swap(attacker, zeroForOne, amount, priceLimit, "", extensionData)
    [msg.sender in pool = router]

  pool calls:
    _beforeSwap(sender=router, ...)

  extension checks:
    allowedSwapper[pool][router] == true  ✓  (bypass succeeds)

  attacker receives swap output — allowlist completely bypassed.
```
Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only the router, call `exactInputSingle` from an unallowlisted EOA, assert the swap succeeds and `attacker` receives output tokens.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-39)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```

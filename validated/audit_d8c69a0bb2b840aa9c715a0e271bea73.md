Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end-user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` — the immediate caller of `pool.swap()` — against the per-pool allowlist. When `MetricOmmSimpleRouter` intermediates a swap, `sender` is the router contract address, not the end-user. Any pool admin who allowlists the router (a required step for legitimate users to access the router) inadvertently opens the allowlist gate to every user, completely defeating the access-control invariant of restricted pools.

## Finding Description

**Root cause — `beforeSwap` checks the wrong actor.**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [1](#0-0) 

**The pool passes its own `msg.sender` as `sender` to `_beforeSwap`.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)`, so `sender` is whoever called `pool.swap()`: [2](#0-1) 

**The router calls `pool.swap(params.recipient, ...)`, making itself `msg.sender`.**

`exactInputSingle` calls `pool.swap` directly with no originating-user argument: [3](#0-2) 

The pool therefore sees `msg.sender = router` and passes `sender = router` to `_beforeSwap`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The same pattern applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

**Why existing guards are insufficient.**

There is no mechanism in the extension, the pool, or the router to propagate the originating user's identity. The `extensionData` field is caller-supplied and unverified, so the router cannot trustlessly encode `msg.sender` there without a coordinated, authenticated design. The pool's `swap` interface accepts only `recipient`, not an explicit `sender`/`originator`: [5](#0-4) 

## Impact Explanation

Any user not on the swap allowlist can bypass the pool's primary access-control boundary by calling any router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) on a pool that has `SwapAllowlistExtension` active and the router address allowlisted. On pools designed for restricted counterparties (KYC-gated, institutional-only), this allows unauthorized principals to trade, violating the pool's invariant and potentially draining LP-owned liquidity at oracle prices. This constitutes a broken core pool access-control mechanism with direct fund-loss potential on restricted pools.

## Likelihood Explanation

Medium. The bypass requires the pool admin to have allowlisted the router. This is a natural and expected configuration: without it, even legitimately allowlisted users cannot use the router. Any pool that wants to support router-based swaps for its approved users must allowlist the router, inadvertently opening the gate to all users. The condition is therefore likely to be met in any real deployment that combines `SwapAllowlistExtension` with `MetricOmmSimpleRouter`.

## Recommendation

The `beforeSwap` hook must check the actual end-user identity, not the intermediary. Two viable approaches:

1. **Router encodes the originating user in `extensionData` with authentication**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it only when `msg.sender` (the pool's caller) is a trusted router. This requires coordinated changes to both the router and the extension, and the extension must maintain a registry of trusted routers.

2. **Pool exposes an explicit `originSender` parameter**: The pool's `swap` function accepts a declared originating user alongside `recipient`, enforces that `msg.sender` is an approved operator for that sender, and passes the declared sender to `_beforeSwap`. The extension then checks the declared sender.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)   // Alice is allowed
  pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so Alice can use it

Attack:
  charlie (not on allowlist) calls:
    router.exactInputSingle({pool: pool, recipient: charlie, ...})

  router calls:
    pool.swap(charlie, zeroForOne, amount, priceLimit, "", extensionData)
    // msg.sender = router

  pool calls (MetricOmmPool.sol L230-240):
    _beforeSwap(router, charlie, ...)

  ExtensionCalling._beforeSwap forwards sender=router to extension (ExtensionCalling.sol L149-177)

  SwapAllowlistExtension.beforeSwap checks (SwapAllowlistExtension.sol L37):
    allowedSwapper[pool][router]  →  true  ✓  (bypass succeeds)

  Charlie's swap executes on the restricted pool.
```

A Foundry integration test can reproduce this by deploying a pool with `SwapAllowlistExtension`, allowlisting only `alice` and the router, then calling `router.exactInputSingle` from `charlie` and asserting the swap succeeds (no `NotAllowedToSwap` revert).

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L188-195)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (int128 amount0Delta, int128 amount1Delta);
```

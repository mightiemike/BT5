Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Enabling Full Per-User Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` validates the `sender` argument, which the pool sets to its own `msg.sender` — the router — when a swap is routed through `MetricOmmSimpleRouter`. Because the router is a single shared contract, allowlisting it to enable router-mediated swaps on a curated pool simultaneously grants every user, including non-allowlisted ones, the ability to bypass the per-user gate. The individual-user allowlist invariant is completely broken for any pool that accepts router calls.

## Finding Description

**Root cause — extension checks router, not user:**

`SwapAllowlistExtension.beforeSwap()` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded from the pool's `_beforeSwap` dispatcher.

**Pool passes its own `msg.sender` as `sender`:**

`MetricOmmPool.swap()` calls:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap()` then encodes that value as the `sender` argument to the extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [3](#0-2) 

**Router never injects the originating user:**

`MetricOmmSimpleRouter.exactInputSingle()` calls the pool directly without forwarding `msg.sender`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The user's address is stored only in transient callback context for payment purposes; it is never passed into `pool.swap()`. The pool therefore sees `msg.sender = router`.

**Exploit path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists only specific users.
2. To allow any router-mediated swap at all, the admin must call `setAllowedToSwap(pool, router, true)`.
3. A non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle()` targeting that pool.
4. The pool receives `msg.sender = router`; the extension evaluates `allowedSwapper[pool][router] == true` and passes.
5. The attacker's swap executes despite never being individually allowlisted.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with `msg.sender = router`. [5](#0-4) 

## Impact Explanation
The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting swap access to a curated pool. When the router is allowlisted (the only way to support router-mediated swaps), the per-user gate is entirely nullified: every address on the network can swap against the pool by routing through `MetricOmmSimpleRouter`. This constitutes broken core pool functionality — the allowlist access control does not protect the pool as intended, allowing unauthorized traders to interact with pools that are supposed to be restricted.

## Likelihood Explanation
The condition is trivially reachable by any unprivileged user. No special privileges, flash loans, or complex setup are required — only a call to a public router function. Any pool that uses `SwapAllowlistExtension` and supports router-mediated swaps is permanently affected. The bypass is repeatable and deterministic.

## Recommendation
Pass the originating user's address through the call chain so the extension can check the actual trader. One approach: add an `originator` field to the pool's `swap()` signature (or a separate transient-storage slot set by the router before calling the pool) and forward it as the `sender` argument to `beforeSwap`. Alternatively, `SwapAllowlistExtension` could maintain a separate allowlist for trusted routers and require routers to attest the originating user via a signed parameter or callback, then validate that attested address instead of `sender`.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension configured.
// 2. Pool admin allowlists only `trustedUser` via setAllowedToSwap(pool, trustedUser, true).
// 3. Pool admin allowlists the router via setAllowedToSwap(pool, router, true)
//    (required for any router-mediated swap to work).
// 4. `attacker` is NOT in the allowlist.

// Attack:
// attacker calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: curatedPool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    deadline: block.timestamp,
    extensionData: ""
}));

// Result:
// pool.swap() is called with msg.sender = router.
// beforeSwap checks allowedSwapper[pool][router] == true → passes.
// Attacker swaps successfully despite not being individually allowlisted.
// Assert: swap completes without NotAllowedToSwap revert.
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L162-165)
```text
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
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

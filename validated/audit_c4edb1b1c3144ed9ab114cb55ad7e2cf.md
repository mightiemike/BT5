Audit Report

## Title
`SwapAllowlistExtension` checks router address as swapper instead of original user, making per-user allowlisting incompatible with `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` always sets to `msg.sender` — the immediate caller of `swap()`. When users route through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originalUser]`. This creates an irresolvable dilemma: allowlisted users cannot use the router, and allowlisting the router to fix that opens a complete bypass for all non-allowlisted users.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks that exact value against the per-pool allowlist: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, so the pool sees `msg.sender = router`: [4](#0-3) 

The same applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181) — in every case the pool receives `msg.sender = router`. No existing guard recovers the original user's address; `extensionData` is passed through unchanged but the extension never reads it for identity purposes. [5](#0-4) 

## Impact Explanation
Two fund-impacting outcomes follow directly:

1. **Broken core swap functionality for allowlisted pools.** A pool admin allowlists specific users (e.g., KYC-verified addresses). Those users call `exactInputSingle` through the router. The extension sees the router address, which is not in the allowlist, and reverts `NotAllowedToSwap`. Allowlisted users cannot use the supported periphery path at all — broken core swap functionality.

2. **Complete allowlist bypass.** To fix (1), the pool admin allowlists the router address (`allowedSwapper[pool][router] = true`). Any non-allowlisted user then calls `exactInputSingle` through the router; the extension sees `sender = router`, passes the check, and the swap executes. The curation policy is entirely defeated — non-allowlisted users trade on a pool designed to exclude them, extracting value from LPs who deposited under the assumption that only vetted counterparties would trade.

Both outcomes meet the Sherlock threshold: broken core pool functionality causing loss of funds or unusable swap flows, and admin-boundary break where an unprivileged path bypasses a pool admin's access control.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entry point in `metric-periphery`. Any pool that deploys `SwapAllowlistExtension` for curation will immediately encounter this mismatch the first time an allowlisted user tries to use the router. The trigger requires no special privileges — any ordinary user calling `exactInputSingle` or `exactInput` on an allowlisted pool reproduces both failure modes. The pool admin's only apparent fix (allowlisting the router) opens the bypass.

## Recommendation
The pool must receive the original initiating user's address and forward it to extensions. Two approaches:

**Option A – Pass originator through `extensionData`.** The router encodes `msg.sender` into `extensionData` for each hop. `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of (or in addition to) `sender`. This requires a convention between router and extension.

**Option B – Add an originator field to the swap interface.** Extend `IMetricOmmPoolActions.swap` with an explicit `originator` parameter. The pool passes it to `_beforeSwap` alongside `sender`. Extensions can then gate on the true economic actor regardless of which intermediary called the pool.

Either way, `SwapAllowlistExtension` must check the address of the user who initiated the transaction, not the address of the contract that called `pool.swap()`.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwapOrder`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowed swapper.
3. Alice calls `router.exactInputSingle(...)` targeting that pool.
4. Inside `pool.swap()`, `msg.sender = router`. The extension checks `allowedSwapper[pool][router]` → `false` → reverts `NotAllowedToSwap`. Alice cannot trade through the router despite being allowlisted.
5. Pool admin calls `setAllowedToSwap(pool, router, true)` to unblock Alice.
6. Bob (not allowlisted) calls `router.exactInputSingle(...)`. The extension checks `allowedSwapper[pool][router]` → `true` → passes. Bob trades on the curated pool, bypassing the allowlist entirely.

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

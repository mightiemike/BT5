Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, allowing any caller to bypass per-user swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is the caller, `sender` is the router address, not the end-user. Any pool admin who allowlists the router to enable router-based trading simultaneously grants every on-chain address access to the pool, completely defeating the per-user access control the extension is designed to enforce.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly without encoding the originating user into the call — the pool sees the router as `msg.sender`: [4](#0-3) 

The same substitution occurs in `exactInput` (L104–112), `exactOutputSingle` (L136–137), and `exactOutput` (L165–181): [5](#0-4) [6](#0-5) [7](#0-6) 

The extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. No existing guard in the extension or pool corrects for this indirection.

## Impact Explanation
A pool admin deploying `SwapAllowlistExtension` intends to restrict trading to specific counterparties (e.g., KYC-verified addresses). To allow those counterparties to use the standard router, the admin calls `setAllowedToSwap(pool, router, true)`. The moment the router is allowlisted, every address on-chain can call `exactInputSingle` and reach the pool, because the extension only sees the router address and approves it. Unauthorized swappers can trade against the pool at oracle-derived bid/ask prices, extracting value from LP positions. This constitutes broken core pool functionality — the access-control invariant the extension is designed to enforce is silently voided, resulting in direct loss of LP principal.

## Likelihood Explanation
Medium-High. The router is the canonical user-facing entry point. Any pool admin who wants allowlisted users to trade through the UI/router will naturally allowlist the router address. The bypass requires no privileged access, no malicious setup, and no non-standard tokens — only a call to the public `exactInputSingle` or `exactInput` function. The admin's own correct operational step (allowlisting the router) is what opens the hole.

## Recommendation
The extension must check the originating user, not the immediate caller of `pool.swap`. Two options:

1. **Pass the real user through the router.** Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value, enforcing a trust check that `sender == trustedRouter` before accepting the decoded identity.

2. **Use transient storage at the pool level.** Have the router write the real user into a transient storage slot before calling `swap`, analogous to how it already uses transient storage for callback context (`_setNextCallbackContext`), and have the pool or extension read that slot.

Either way, the extension must gate the economically relevant actor — the address whose funds are being used — not the contract that happens to be the immediate `msg.sender` of `pool.swap`.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router users
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ..., extensionData: ""})
  - Router calls pool.swap(recipient, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes
  - Swap executes; attacker receives output tokens

Result:
  - attacker swapped against the pool despite never being allowlisted
  - isAllowedToSwap(pool, attacker) returns false, yet the swap succeeded
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L165-181)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );
```

Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end user, enabling full allowlist bypass for any caller routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `MetricOmmPool.swap`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Any pool admin who allowlists the router address — the natural configuration for a curated pool intended to be used through the standard periphery — grants every on-chain address the ability to swap in that pool, completely neutralizing the allowlist guard.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the `sender` argument to every configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`: [2](#0-1) 

**Step 2 — The router is `msg.sender` to the pool, not the end user.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, so the pool sees `msg.sender = router`: [3](#0-2) 

The same applies to `exactOutputSingle` (L136-137), `exactInput` (L104-112), `exactOutput` (L165-181), and `_exactOutputIterateCallback` (L220-228). [4](#0-3) 

**Step 3 — The extension checks the router address, not the user.**

`SwapAllowlistExtension.beforeSwap` evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [5](#0-4) 

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the router address. The lookup becomes `allowedSwapper[pool][router]`. Every user who routes through the same router contract presents the identical identity to the guard.

**Step 4 — Existing guards are insufficient.**

There is no mechanism in `SwapAllowlistExtension` to distinguish individual end users when they arrive via the router. The `extensionData` field is passed through but never inspected by the extension. The `setAllowedToSwap` and `setAllowAllSwappers` setters only operate on the flat `allowedSwapper[pool][address]` mapping, which cannot express per-user policy when the intermediary is the router. [6](#0-5) 

## Impact Explanation
A pool admin who allowlists the router address — the natural and expected configuration for a curated pool intended to be used through the standard periphery — grants every address on-chain the ability to swap in that pool. The swap allowlist guard is completely neutralized. Any disallowed user can trade in the curated pool, receiving output tokens at the oracle-derived bid/ask price. This is a direct admin-boundary break: the pool admin's intended access control policy is bypassed by an unprivileged path with no preconditions. The pool's curation invariant is broken and the LP's exposure is no longer limited to the intended counterparty set, constituting a fund-impacting policy bypass meeting the High severity threshold.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entry point. A pool admin who deploys a curated pool and wants allowlisted users to have normal UX will allowlist the router. The bypass is immediately reachable by any unprivileged address with no special tokens, no malicious setup, and no privileged access — only a call to `router.exactInputSingle` (or any other router entry point) is required. The precondition (router allowlisted) is the expected production configuration, making this highly likely to be triggered.

## Recommendation
The extension must check the economically relevant actor — the end user — not the intermediary router. Two sound approaches:

1. **Embed the original caller in `extensionData`.** The router stores the original `msg.sender` in transient storage (already done for the payer context via `_setNextCallbackContext`). The router should embed the user address in a standardized prefix of `extensionData`, and the extension should decode and check that value when the pool-level `sender` is a known router.

2. **Trusted-forwarder pattern.** The extension inspects whether `sender` is a factory-registered router and, if so, requires the user address to be embedded in `extensionData` with a trusted-forwarder signature or registry check.

The simplest safe fix: have the router prepend `abi.encode(msg.sender)` to `extensionData` before passing it to `pool.swap`, and have `SwapAllowlistExtension.beforeSwap` decode and check that value when `sender` is a known router address.

## Proof of Concept
```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted for UX
4. Bob (disallowed) calls router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(recipient, ...)
     [pool.swap L231: msg.sender = router]
   → pool calls _beforeSwap(sender=router, ...)
     [ExtensionCalling.sol L163-165]
   → extension checks allowedSwapper[pool][router] → TRUE
     [SwapAllowlistExtension.sol L37]
   → swap executes for Bob with no revert
5. Bob receives output tokens from the curated pool despite being explicitly disallowed.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-29)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

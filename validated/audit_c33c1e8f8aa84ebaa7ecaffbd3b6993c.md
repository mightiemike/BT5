Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Originating User, Allowing Any Caller to Bypass the Per-Pool Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` from the pool's perspective — the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating EOA. A pool admin who allowlists the router to enable router-mediated swaps for approved users inadvertently grants swap access to every user who calls the router, completely nullifying the allowlist.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the calling pool: [1](#0-0) 

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` from the pool's perspective: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. Once the router is allowlisted (which is required for any approved user to use it), the check passes for every caller of the router regardless of their identity. No existing guard in the extension or pool recovers the originating EOA.

## Impact Explanation
Any unprivileged user can bypass a `SwapAllowlistExtension`-gated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The allowlist is the sole access-control mechanism for these pools — intended to restrict trading to approved counterparties for compliance, risk management, or exclusive-access purposes. With the router allowlisted, the restriction is completely nullified: blocked users can execute swaps against pool liquidity at oracle-anchored prices that were only intended for approved counterparties, constituting a direct loss of the pool's intended access-control invariant and potential fund impact to LPs.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard publicly deployed periphery contract; any EOA can call it. A pool admin who wants approved users to trade via the router **must** allowlist the router — there is no alternative mechanism. The bypass requires no special privileges, no malicious setup, and no non-standard tokens. Any EOA can trigger it repeatably with a single router call.

## Recommendation
The extension must check the originating user, not the direct pool caller. The cleanest production fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode and check that field when `sender` is a known/trusted router. Alternatively, the pool could forward a dedicated `originator` field through the swap call. Using `tx.origin` is acceptable only for EOA-only pools and introduces its own risks. The router already stores the real payer in transient storage (`_setNextCallbackContext(..., msg.sender, ...)`); a similar mechanism could forward the originating user to the extension. [5](#0-4) 

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is approved
  - Pool admin calls setAllowedToSwap(pool, router, true)  // required for alice to use router

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(...) → pool sees msg.sender = router
  - pool calls extension.beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router] → true
  - bob's swap executes successfully despite not being on the allowlist

Result:
  - bob trades against a pool restricted to approved counterparties
  - The allowlist invariant is broken; any user with router access can trade
```

### Citations

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

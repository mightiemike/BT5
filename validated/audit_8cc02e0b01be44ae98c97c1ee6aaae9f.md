The code confirms the claim at every step of the call chain. The finding is valid.

Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass Swap Allowlist via MetricOmmSimpleRouter - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on `sender`, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. A pool admin who allowlists the router to support router-mediated swaps inadvertently opens the pool to every user who calls through the router, completely defeating the allowlist.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded from the pool: [1](#0-0) 

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that `sender` value directly into the extension call: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` without encoding the original `msg.sender` anywhere the extension can see — the pool receives `msg.sender = router`: [4](#0-3) 

The `_setNextCallbackContext` call on line 71 stores `msg.sender` in transient storage only for the payment callback, not for the extension check. The `params.extensionData` forwarded to the pool is user-supplied and unvalidated — there is no mechanism that enforces the actual caller identity into `extensionData`. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][attacker]`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the position owner explicitly passed by the caller), which is preserved through the liquidity adder path and is not substituted by an intermediary address: [5](#0-4) 

## Impact Explanation
A curated pool (KYC-only, institutional-only, partner-restricted) using `SwapAllowlistExtension` that allowlists the router to support standard periphery access is fully open to any user who calls through the router. The allowlist provides zero protection on the router path. Unauthorized traders can execute swaps against the pool's oracle-anchored liquidity, exposing LPs to trades they explicitly intended to restrict. In oracle-anchored pools, LP funds are at risk from unauthorized arbitrageurs or adversarial traders who should have been blocked. This constitutes a broken core pool access-control mechanism causing potential direct loss of LP assets.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard, documented periphery swap entrypoint. Any pool admin who wants to allow router-mediated swaps (the normal user flow) must allowlist the router. The bypass is reachable through the standard supported periphery path with no special setup beyond the natural admin configuration. No privileged attacker capability is required — any EOA can call `exactInputSingle` through the router.

## Recommendation
`SwapAllowlistExtension.beforeSwap` should gate on the actual end-user identity, not the direct caller of `swap()`. The cleanest fix is to require the router to encode the original `msg.sender` in `extensionData` and have the extension decode and verify it when `sender` is a known router address. Alternatively, document that the router must never be allowlisted and that allowlisted users must call the pool directly — but this is a design constraint that is not enforced on-chain and is easily violated by pool admins.

## Proof of Concept
```
Setup:
- Deploy pool with SwapAllowlistExtension configured
- Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist the router for normal usage
- Pool admin does NOT allowlist attacker EOA

Attack:
- attacker (non-allowlisted EOA) calls router.exactInputSingle({pool: pool, ...})
- router calls pool.swap(recipient, zeroForOne, ...) with msg.sender = router
- pool calls _beforeSwap(sender=router, ...)
- extension checks allowedSwapper[pool][router] → true → PASSES
- attacker's swap executes against the curated pool

Expected: revert NotAllowedToSwap
Actual: swap succeeds — allowedSwapper[pool][attacker] is never checked
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-166)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

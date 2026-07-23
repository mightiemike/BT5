Audit Report

## Title
`SwapAllowlistExtension` checks router address as swapper identity, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the direct caller of the pool. When swaps are routed through `MetricOmmSimpleRouter`, the router's address is presented as the swapper identity, not the end-user's. A pool admin who allowlists the router to enable router-mediated access for curated users inadvertently opens the pool to every user, completely defeating the per-user allowlist.

## Finding Description

**Step 1 — Pool binds `sender` to `msg.sender`:**
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

**Step 2 — Extension framework forwards `sender` verbatim:**
`ExtensionCalling._beforeSwap` encodes and forwards that value unchanged to every configured extension: [2](#0-1) 

**Step 3 — Extension checks the forwarded `sender`:**
`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [3](#0-2) 

**Step 4 — Router never forwards the end-user:**
`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The router is `msg.sender` of the pool call; the actual end-user (`msg.sender` of the router call) is never passed to the pool or extension: [4](#0-3) 

The same applies to `exactOutputSingle` (L136-137), `exactInput` (L104-112), and `exactOutput` (L165-181). [5](#0-4) 

**Contrast with `DepositAllowlistExtension`:**
The deposit-side extension correctly gates by `owner` — the position owner explicitly passed as a parameter — which is stable regardless of the call path: [6](#0-5) 

The swap-side extension has no equivalent stable identity; it relies on `sender`, which changes depending on the entry path.

**Wrong value:** `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][end_user]`. The extension returns `IMetricOmmExtensions.beforeSwap.selector` (approval) for any user routing through an allowlisted router address.

## Impact Explanation
When the router is allowlisted — the only way to let curated users trade via the standard periphery — any non-allowlisted user can execute swaps on the curated pool by calling any router entry point (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`). The pool settles real token transfers against LP reserves at oracle-anchored prices. Non-allowlisted users can drain LP assets, extract value at prices the pool admin intended only for specific counterparties (e.g., KYC-verified users), or violate compliance requirements the allowlist was meant to enforce. This is direct loss of LP principal and a broken core pool invariant (curated access control). The bypass is silent — no event is emitted by the extension, and the swap completes normally.

## Likelihood Explanation
The bypass requires the admin to allowlist the router address. This is the natural and expected configuration step for any curated pool that should be accessible via the protocol's standard periphery: without allowlisting the router, even the intended allowlisted users cannot trade through `MetricOmmSimpleRouter`. The admin faces an impossible choice — either allowlist the router (opening the pool to everyone) or do not (blocking allowlisted users from using the router). Any pool operator who follows the natural setup path triggers the bypass. No special attacker capability is required beyond calling the public router.

## Recommendation
`SwapAllowlistExtension` must gate on the actual end-user identity, not the direct caller of the pool. Two viable approaches:

1. **Extension-data identity forwarding:** Require the router to encode the originating user address in `extensionData`, and have `SwapAllowlistExtension` decode and check that address. The extension should reject calls where the encoded identity does not come from a trusted forwarding contract (verified via a registry or immutable allowlist of trusted routers).

2. **Explicit `originator` parameter:** Add an `originator` field to the pool's `swap` signature (analogous to `owner` in `addLiquidity`) that the router populates with `msg.sender` before calling the pool. The extension then checks `allowedSwapper[pool][originator]` instead of `allowedSwapper[pool][sender]`.

Either approach must ensure the originator field cannot be spoofed by an untrusted caller.

## Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin calls swapExt.setAllowedToSwap(pool, router, true)   // allowlist the router
  admin calls swapExt.setAllowedToSwap(pool, alice, true)    // allowlist alice
  // bob is NOT allowlisted

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)   [msg.sender = router]
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] → TRUE
    → swap executes, bob receives output tokens
    → allowlist completely bypassed

Verification:
  // Direct call by bob (without router) correctly reverts:
  bob calls pool.swap(...) directly
    → pool calls _beforeSwap(sender=bob, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][bob] → FALSE
    → revert NotAllowedToSwap()
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, configure allowlist with only `alice` and `router`, call `router.exactInputSingle` as `bob`, assert swap succeeds and `bob` receives output tokens despite not being individually allowlisted.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
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

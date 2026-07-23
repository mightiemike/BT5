Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address as Swapper, Allowing Any User to Bypass Per-User Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the originating user. Any pool admin who allowlists the router (required for legitimate users to use it) simultaneously grants every unprivileged user the ability to bypass the per-user allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards it unchanged as the first argument to every registered extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) [6](#0-5) 

This creates an irresolvable dilemma: if the pool admin does **not** allowlist the router, no user can swap via the router (the router is the `msg.sender` of `pool.swap()`, so it must be allowlisted). If the pool admin **does** allowlist the router, the check `allowedSwapper[pool][router] == true` passes for every user who routes through it, regardless of whether that user is individually allowlisted.

## Impact Explanation
A curated pool (KYC-gated, regulatory-restricted, or counterparty-limited) deploying `SwapAllowlistExtension` has its access control completely bypassed by any user who calls the public router. The core invariant — "only addresses explicitly approved by the pool admin may swap" — is broken. This constitutes an admin-boundary break via an unprivileged periphery path, with direct impact on pool access control and potential regulatory/compliance consequences for restricted pools.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin enabling router-based swaps for legitimate users must allowlist the router, at which point the bypass is open to all. No special privilege or setup is required — a standard public router call suffices. The condition is met in normal operational use.

## Recommendation
The extension must identify the **actual originating user**, not the immediate caller of `pool.swap()`. The preferred fix is pool-level identity forwarding: the router writes the originating `msg.sender` into a transient storage slot before calling `pool.swap()`, and the extension reads it. This is consistent with how `MetricOmmSwapRouterBase` already uses transient storage for callback context. Alternatively, the router can encode the real user into `extensionData`, and the extension can decode and verify it — though this requires the extension to trust the encoding.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  - Pool admin calls setAllowedToSwap(pool, router, true)  // required for router usage

Attack (by bob, not allowlisted):
  1. bob calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender of pool.swap() = router
  3. Pool calls extension.beforeSwap(router, recipient, ...)
  4. Extension checks allowedSwapper[pool][router] == true  → passes
  5. bob's swap executes successfully despite not being on the allowlist

Result: bob bypasses the per-user allowlist and trades in the curated pool.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

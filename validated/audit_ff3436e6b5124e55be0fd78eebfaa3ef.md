Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the real swapper, making the per-pool allowlist bypassable through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives the `sender` argument from the pool, which is always `msg.sender` of the `pool.swap` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This makes the allowlist either universally bypassable (if the router is allowlisted) or universally broken for router users (if it is not), on any curated pool that deploys this extension.

## Finding Description
`MetricOmmPool.swap` unconditionally passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address when the user entered through the router: [2](#0-1) 

All four router entry points call `pool.swap` directly, making the router the `msg.sender` to the pool. For `exactInputSingle`: [3](#0-2) 

For `exactOutputSingle`: [4](#0-3) 

Critically, none of the router entry points encode `msg.sender` into `extensionData` before calling the pool — the user-supplied `params.extensionData` is forwarded verbatim, so the extension has no in-band channel to recover the real initiator. The `allowedSwapper` mapping is keyed by the pool and the immediate pool caller, not the originating user: [5](#0-4) 

Two concrete failure modes result:
1. **Allowlist bypass:** If the pool admin allowlists the router (the natural operational choice), every user — including those explicitly denied — can swap through the router and pass the check.
2. **Legitimate users blocked:** If the pool admin does not allowlist the router, every individually allowlisted user is blocked from using the router even though they are permitted to swap directly.

## Impact Explanation
On any curated pool that deploys `SwapAllowlistExtension` and expects to serve users through `MetricOmmSimpleRouter`, the allowlist is either universally bypassed or universally broken for router users. Disallowed users can execute swaps and receive output tokens they should not be able to obtain, directly violating the pool admin's access-control intent. This constitutes broken core pool functionality: the pool cannot simultaneously enforce its curated-access policy and support the standard periphery swap path, meeting the "Broken core pool functionality causing loss of funds or unusable swap flows" impact criterion.

## Likelihood Explanation
The router is the documented, primary user-facing entry point for swaps. Any user who discovers the discrepancy can bypass the allowlist by calling `exactInputSingle` instead of `pool.swap` directly. No privileged access, special tokens, or unusual setup is required — only a standard router call. The bypass is reachable on every curated pool that has `SwapAllowlistExtension` configured and does not explicitly block the router at the network level.

## Recommendation
The extension must recover the original user identity rather than relying on the `sender` argument. The simplest correct fix: the router encodes `msg.sender` into `extensionData` before calling the pool (e.g., as an ABI-encoded prefix or a dedicated struct field), and `SwapAllowlistExtension.beforeSwap` decodes and checks that field as the authoritative swapper identity when `sender` is a known router address. Alternatively, add a `swapInitiator` field to the `extensionData` payload that the router always populates with `msg.sender`, and the extension decodes it unconditionally. Either approach requires a coordinated encoding convention between the router and the extension.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured on the `beforeSwap` order.
2. Call `setAllowedToSwap(pool, router, true)` — the natural admin action to enable router-mediated swaps.
3. As a user address that has **not** been individually allowlisted, call `router.exactInputSingle(...)` targeting the pool.
4. Observe the swap succeeds: `allowedSwapper[pool][router] == true` passes the check even though the real user is not on the allowlist.
5. Alternatively, call `pool.swap(...)` directly as the same user and observe `NotAllowedToSwap` reverts — confirming the allowlist is enforced only on the direct path, not through the router.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

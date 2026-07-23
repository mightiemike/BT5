Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the direct pool caller. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If the pool admin allowlists the router to enable curated access via the official periphery, every unprivileged user can bypass the allowlist by routing through the router.

## Finding Description

**Root cause — `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:** [1](#0-0) 

**`ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router when routing through `MetricOmmSimpleRouter`:** [3](#0-2) 

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original user's identity — `msg.sender` (the actual user) is only stored in transient callback context for payment, never passed to the pool as the economic actor:** [4](#0-3) 

**`DepositAllowlistExtension` does not share this flaw because it checks `owner` (the second argument), which callers supply explicitly as the actual liquidity owner:** [5](#0-4) 

The asymmetry is structural: `beforeAddLiquidity` receives an explicit `owner` parameter that correctly identifies the economic actor, while `beforeSwap`'s `sender` is the direct pool caller, which is the router when the official periphery is used.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise curated addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The bypass requires no special privileges: any unprivileged user calls the public router targeting the curated pool. Unauthorized traders can move pool prices, extract value against LP positions, and drain liquidity — constituting a direct, fund-impacting policy bypass on curated pools. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation

Medium-to-high. `MetricOmmSimpleRouter` is the official swap periphery. Pool admins who configure a swap allowlist will naturally also allowlist the router so their curated users can access the pool through the standard interface. Once the router is allowlisted, the bypass is trivially reachable by any unprivileged user with no special setup, no flash loans, and no elevated permissions.

## Recommendation

The extension must gate the original user, not the direct pool caller. Viable approaches:

1. **Pass the original user via `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. Requires a trusted encoding convention.
2. **Preferred — propagate `originalSender` at the pool level:** Add a first-class `originalSender` field that `MetricOmmPool` propagates through hook arguments, so extensions always see the economic actor regardless of intermediary.
3. **Router-aware allowlist:** Extend the extension to recognize approved router contracts and, when `sender` is a known router, require the router to attest the original user via a signed payload in `extensionData`.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as extension
  admin: allowedSwapper[pool][router] = true   // router allowlisted for curated access
  alice (allowlisted directly): allowedSwapper[pool][alice] = true
  bob (NOT allowlisted): allowedSwapper[pool][bob] = false

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...)          // msg.sender = router
  → MetricOmmPool._beforeSwap(sender=router, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  → allowedSwapper[pool][router] → true
  → swap executes for bob with no revert

Result:
  bob, a non-allowlisted user, successfully swaps on a curated pool.
  The allowlist invariant is broken.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only the router and alice, call `exactInputSingle` as bob, assert no revert and that bob's swap settles.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-176)
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

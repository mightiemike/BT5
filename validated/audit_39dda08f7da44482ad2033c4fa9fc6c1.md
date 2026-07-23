Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any caller to bypass the swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the value passed by the pool — which is the pool's own `msg.sender`, i.e., whoever called `pool.swap()`. When `MetricOmmSimpleRouter` intermediates a swap, it calls `pool.swap()` directly, making the router the `sender` seen by the extension. If the pool admin allowlists the router (the necessary step for any allowlisted user to trade through the standard periphery), every unprivileged user can bypass the allowlist by routing through `MetricOmmSimpleRouter`.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router contract the `msg.sender` inside the pool: [3](#0-2) 

The original end user's identity is stored only in transient callback context for payment purposes and is never forwarded to the pool or extension: [4](#0-3) 

Consequently, the extension evaluates `allowedSwapper[pool][router]` for every router-mediated swap, regardless of who the actual end user is. There is no existing guard that recovers the originating user's identity from the router call.

## Impact Explanation

A pool admin who configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties) must also allowlist the router if those users are expected to trade through the standard periphery. Once the router is allowlisted, the gate is open to every address on-chain. Any user calls `MetricOmmSimpleRouter.exactInputSingle` and the extension passes because it sees the allowlisted router, not the unauthorized caller. The allowlist provides zero protection against router-mediated swaps, allowing unauthorized parties to trade on a pool that was explicitly configured to exclude them. This constitutes a broken core pool functionality (admin-configured access control bypassed by an unprivileged path) with direct fund-impact: unauthorized parties execute swaps and extract tokens from a restricted pool.

## Likelihood Explanation

The scenario is reachable by any unprivileged user with no special setup. The only precondition — the pool admin having allowlisted the router — is the natural and necessary step any admin must take to let their legitimate users trade through the standard periphery. `MetricOmmSimpleRouter` is a public, permissionless contract, so no collusion or privileged access is required. The attack is repeatable indefinitely.

## Recommendation

The extension must verify the originating user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it when `sender` is a known trusted router. This requires the extension to maintain a registry of trusted routers.

2. **Require direct pool calls for allowlisted pools**: Document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router, and allowlisted users must call `pool.swap()` directly. This is the simplest fix but breaks router UX for allowlisted pools.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)       // alice is KYC'd
  pool admin calls setAllowedToSwap(pool, router, true)      // needed so alice can use the router

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})

  Execution:
    router → pool.swap(recipient, ...)
      msg.sender inside pool = router
      _beforeSwap(sender=router, ...)
        SwapAllowlistExtension.beforeSwap(sender=router, ...)
          allowedSwapper[pool][router] == true  ✓  (passes)
    swap executes for bob despite bob not being allowlisted
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

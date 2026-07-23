Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call — the router contract, not the end user. When the router is allowlisted (required for any router-mediated swap to succeed), every non-allowlisted user can bypass the per-user gate by routing through `MetricOmmSimpleRouter`. This renders the extension's access control ineffective for any pool that permits router usage.

## Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, passing the immediate caller as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` directly to the extension's `beforeSwap` hook: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(...)` with `msg.sender = router`: [4](#0-3) 

So `sender` in the extension is the **router address**, not the end user. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an impossible choice: not allowlisting the router blocks all router-mediated swaps (including for legitimately allowlisted users), while allowlisting the router opens the gate to every user on earth.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the second parameter, the actual position owner), not `sender`: [5](#0-4) 

The `beforeSwap` hook interface does not expose an equivalent `owner`/`originator` parameter distinct from `sender`, so there is no in-band way for the extension to recover the true end user without out-of-band data (e.g., `extensionData`).

## Impact Explanation

A pool configured with `SwapAllowlistExtension` for KYC, compliance, or counterparty-restriction purposes provides zero protection against any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps on a curated pool, draining liquidity or extracting value that the pool admin intended to restrict. This is a direct bypass of a configured access-control guard with fund-impacting consequences, meeting the "broken core pool functionality causing loss of funds" threshold.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who deploys `SwapAllowlistExtension` and wants allowlisted users to use the router (the normal production path) must allowlist the router, immediately opening the bypass to all users. No special timing, privileged role, or unusual token behavior is required — any public user can exploit it on every swap.

## Recommendation

The extension must check the actual end-user address, not the intermediate router. Two viable approaches:

1. **Extension-data forwarding**: Have `MetricOmmSimpleRouter` encode `msg.sender` (the actual user) into `extensionData` before forwarding to the pool. The extension decodes and checks that address instead of `sender`. This requires the extension to trust the router as a source of truth for the encoded user address.

2. **Dedicated originator parameter**: Add a separate `originalSender` field to the `beforeSwap` hook interface that the pool populates from a trusted periphery-supplied value, keeping `sender` as the immediate caller for callback-settlement purposes — mirroring how `beforeAddLiquidity` exposes `owner` separately from `sender`.

The `DepositAllowlistExtension` pattern (checking `owner`, a parameter distinct from `sender`) is the correct model to follow. [6](#0-5) 

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is intended to be allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — required to let Alice use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, ...)`, extension evaluates `allowedSwapper[pool][router]` → `true`.
7. Bob's swap succeeds — he has bypassed the per-user allowlist on a curated pool. [7](#0-6)

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

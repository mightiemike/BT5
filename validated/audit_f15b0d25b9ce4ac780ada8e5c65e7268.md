Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Real User, Allowing Any User to Bypass the Configured Allowlist via the Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (required for any router-mediated swap), every user — including those the admin intended to block — can bypass the allowlist by calling the router instead of the pool directly.

## Finding Description

**Root cause 1 — Pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`:**

In `MetricOmmPool.swap`, the pool unconditionally passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

When the call originates from `MetricOmmSimpleRouter`, `msg.sender` is the router contract address, not the end user.

**Root cause 2 — `SwapAllowlistExtension.beforeSwap` checks `sender` (the router), not the real user:**

The extension checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router address forwarded by the pool: [2](#0-1) 

**Router call path — `exactInputSingle` calls `pool.swap` as `msg.sender = router`:**

The router calls `pool.swap(params.recipient, ...)` directly with no mechanism to propagate the original caller: [3](#0-2) 

**Multi-hop path has the same flaw:**

For intermediate hops in `exactInput`, the pool still sees `msg.sender = router` (address(this)): [4](#0-3) 

The extension has no mechanism to distinguish between a direct call from an allowlisted user and a router-mediated call from a non-allowlisted user. The `extensionData` field passed to the pool is forwarded as-is from the caller with no attestation of the real sender. [5](#0-4) 

## Impact Explanation

This is an admin-boundary break: a pool admin configures `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified counterparties). The invariant — only allowlisted addresses may swap — is completely neutralized. Any non-allowlisted user can call `router.exactInputSingle(...)`, the extension evaluates `allowedSwapper[pool][router] = true`, and the swap executes. Unauthorized swaps execute against pool liquidity, directly impacting fund flows and breaking the access control guarantee the admin configured.

## Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract — any EOA or contract can call it. A pool admin who deploys `SwapAllowlistExtension` and wants allowlisted users to use the router (the primary UX entry point) must allowlist the router, which immediately opens the bypass to all users. No special privilege, flash loan, or oracle manipulation is required — a single `exactInputSingle` call suffices.

## Recommendation

The extension must verify the original end-user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real sender through `extensionData`**: Have the router ABI-encode `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires a convention between the router and the extension but needs no pool changes.

2. **Detect router calls and require `extensionData` attestation**: The extension can detect that `sender` is a known router and require a signed or encoded real-user identity in `extensionData`, rejecting calls where the router is the sender but no valid attestation is present.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only allowed swapper
  allowedSwapper[pool][router] = true  // admin must set this for alice to use the router

Attack (executed by bob, who is NOT allowlisted):
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)   // msg.sender = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] → TRUE
    → swap executes successfully for bob

Result:
  bob swaps against pool liquidity despite never being allowlisted.
  The SwapAllowlistExtension guard is fully bypassed.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, set `allowedSwapper[pool][alice] = true` and `allowedSwapper[pool][router] = true`, call `router.exactInputSingle` from a non-allowlisted address `bob`, and assert the swap succeeds (demonstrating the bypass).

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

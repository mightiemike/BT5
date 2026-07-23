Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass Per-User Swap Restrictions via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` validates the `sender` parameter, which resolves to the direct caller of `pool.swap()`. When users interact through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual end user. Any pool that allowlists the router (required for legitimate users to use it) becomes fully open to any user who routes through the router, completely defeating the per-user allowlist.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller), and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the end user
```

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        ...
        params.extensionData
    );
```

This means the call chain is:
```
User → MetricOmmSimpleRouter.exactInputSingle() → pool.swap(msg.sender=router) → extension.beforeSwap(sender=router)
```

The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. This creates an irreconcilable conflict: if the router is not allowlisted, no user can swap through it (including legitimate ones); if the router is allowlisted, every user can swap through it regardless of individual allowlist status.

## Impact Explanation
Any pool deploying `SwapAllowlistExtension` for per-user access control (KYC-gated, institutional-only, curated counterparty sets) is rendered fully permissionless for any user willing to call `MetricOmmSimpleRouter`. The admin-set allowlist invariant — that only explicitly approved addresses may swap — is bypassed by an unprivileged path with no special privileges required. This is a direct admin-boundary break where an unprivileged trader circumvents a pool admin's explicit access restriction.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless periphery contract callable by any EOA or contract. The bypass requires no flash loan, no special role, and no multi-step setup. Any pool that allowlists the router (which is the necessary precondition for legitimate users to use the router at all) is immediately and permanently vulnerable. The attacker only needs to call `exactInputSingle` with the target pool address.

## Recommendation
The extension must check the actual end user, not the direct pool caller. Two viable approaches:

1. **Pass the real user in `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it, enforcing that `sender` (the direct caller) is a factory-registered trusted router before accepting the forwarded identity.

2. **Trusted router registry**: Maintain a registry of trusted routers in the extension. When `sender` is a trusted router, decode the actual user from `extensionData`; otherwise check `sender` directly against the allowlist.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only user A is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so user A can use it.
4. User B (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(sender=router, ...)`.
7. The extension checks `allowedSwapper[pool][router]` → `true`.
8. User B's swap executes successfully, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

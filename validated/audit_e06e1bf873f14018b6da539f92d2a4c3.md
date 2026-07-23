Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of actual swapper, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` validates the `sender` argument, which is `msg.sender` from the pool's perspective — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool receives `sender = router`. If the pool admin allowlists the router (required for any allowlisted user to trade via the router), every non-allowlisted user can bypass the gate by routing through the same public contract.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs the check at line 37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool.

In `MetricOmmPool.swap` (lines 230–240), the pool passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient, ...
);
```

In `MetricOmmSimpleRouter.exactInputSingle` (lines 71–80), the router calls `pool.swap()` on the user's behalf:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The pool therefore calls `extension.beforeSwap(router, ...)`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The actual user's identity is never verified. The same pattern applies to `exactOutputSingle` (line 135) and `exactInput` (line 103).

This creates an irreconcilable dilemma: if the admin does not allowlist the router, allowlisted users cannot use the router at all; if the admin allowlists the router, every user — including non-allowlisted ones — can bypass the gate via the public router. [1](#0-0) [2](#0-1) [3](#0-2) 

## Impact Explanation

Any user not on the allowlist can execute swaps on a restricted pool by routing through `MetricOmmSimpleRouter`. The allowlist — the sole access-control boundary for swap permissions — is rendered ineffective the moment the router is allowlisted. Pools intended for specific counterparties (KYC-gated, market-maker-only, or compliance-restricted) are fully open to arbitrary swappers via the public router. This constitutes a broken core pool functionality (access control) causing unauthorized swap execution, matching the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" allowed impact. [4](#0-3) 

## Likelihood Explanation

The bypass requires the pool admin to have allowlisted the router address. This is a natural and expected operational step: any pool that wants to support router-mediated swaps for its allowlisted users must allowlist the router. The bypass is therefore reachable in any production deployment that uses both `SwapAllowlistExtension` and `MetricOmmSimpleRouter`. No special privileges or unusual conditions are required for the attacker — only a standard call to `exactInputSingle` or any other router entry point. [5](#0-4) 

## Recommendation

The extension must verify the identity of the economic actor, not the intermediary. Viable approaches:

1. **Pass the real user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. This requires a trust assumption that the router is the only allowlisted intermediary and that it faithfully encodes the real caller.
2. **Allowlist at the router level**: The router enforces the allowlist before calling `pool.swap()`, and the extension only allowlists the router itself. The router's entry points check the caller against a separate allowlist before forwarding.
3. **Remove router allowlisting entirely**: Only allowlist EOAs. Allowlisted users must call `pool.swap()` directly. Document this constraint explicitly. [6](#0-5) 

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin: setAllowedToSwap(pool, alice, true)      // alice is a legitimate counterparty
  admin: setAllowedToSwap(pool, router, true)     // needed so alice can use the router

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: charlie, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(charlie, zeroForOne, amount, ...)   // msg.sender = router
      → _beforeSwap(router, charlie, ...)
        → extension.beforeSwap(router, charlie, ...)
          → allowedSwapper[pool][router] == true    // ✓ passes
      → swap executes at oracle price
      → charlie receives output tokens

Result:
  charlie swaps successfully on a pool he is not allowlisted for.
  The allowlist is bypassed.
``` [7](#0-6) [8](#0-7) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User on Router-Mediated Swaps, Enabling Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of the pool call, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool that allowlists the router — the natural configuration for pools intended to support standard periphery access — is fully bypassed by any unprivileged user.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // whoever called pool.swap()
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct), and `sender` is whoever called `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The pool's `msg.sender` is the router, not the user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

No existing guard compensates: `_beforeSwap` in `ExtensionCalling` faithfully forwards the `sender` argument as received from `MetricOmmPool.swap`, and `SwapAllowlistExtension` has no mechanism to distinguish a router-forwarded call from a direct call.

## Impact Explanation

A curated pool's allowlist is its primary access-control boundary. If the pool admin allowlists the router (the standard, documented periphery entry point), every unprivileged user can bypass the individual allowlist by routing through `MetricOmmSimpleRouter`. The extension sees the router as the swapper and passes the check, allowing non-KYC'd or otherwise excluded addresses to trade on a pool explicitly configured to exclude them. This constitutes a direct loss of LP principal and protocol fees through arbitrage or front-running that the allowlist was designed to prevent. Severity: **High**.

## Likelihood Explanation

Pool admins who deploy a curated pool and want users to interact via the standard `MetricOmmSimpleRouter` will naturally add the router to the allowlist. The router is a public, permissionless contract — any address can call it. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges or capital requirements. The condition is a natural and expected operational configuration, making exploitation highly likely.

## Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediary router. Preferred approaches:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. Requires a coordinated convention between router and extension.
2. **Trusted-forwarder / `swapFor` pattern:** Add a `swapFor(address realUser, ...)` entry point or a trusted-forwarder mechanism so the pool can distinguish the originating user from the routing intermediary.
3. **Check `recipient` instead of `sender`:** The recipient receives output tokens and is the economically relevant party, though this changes semantics for multi-hop paths where the router is the intermediate recipient.

## Proof of Concept

```
Setup:
  - Pool P configured with SwapAllowlistExtension E
  - Pool admin calls E.setAllowedToSwap(P, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker address

Attack:
  1. Attacker (non-allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
     → pool's msg.sender = router
  3. Pool calls _beforeSwap(msg.sender=router, ...)  [MetricOmmPool.sol L230-231]
  4. ExtensionCalling dispatches to E.beforeSwap(sender=router, ...)
  5. E checks: allowedSwapper[P][router] == true  → passes  [SwapAllowlistExtension.sol L37]
  6. Swap executes; attacker receives output tokens

Result: Non-allowlisted attacker successfully swaps on a curated pool.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

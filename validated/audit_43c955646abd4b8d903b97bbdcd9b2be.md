Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any caller to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap()` receives the `sender` argument forwarded from `MetricOmmPool.swap()`, which is always `msg.sender` of the pool call — the router contract when users route through `MetricOmmSimpleRouter`. Because the router does not forward the original caller's identity into the pool call, the extension can only observe the router address. Any pool admin who allowlists the router to support router-mediated swaps inadvertently grants every user — including those not individually allowlisted — the ability to bypass the per-user access gate.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← always the immediate caller (router when routed)
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap()` forwards this value unchanged via `abi.encodeCall` to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap()` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` = pool):

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` with no mechanism to forward the original `msg.sender` (the end user):

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // ← no user identity encoded here
  );
```

The complete call chain is:
```
User → router.exactInputSingle()          [msg.sender = user]
     → pool.swap()                        [msg.sender = router]
     → _beforeSwap(sender = router, ...)
     → extension.beforeSwap(sender = router, ...)
     → allowedSwapper[pool][router]       ← checks router, not user
```

The end user's identity is completely lost. This creates an irreconcilable dilemma for pool admins:

| Admin action | Effect |
|---|---|
| Allowlist individual users only | Allowlisted users **cannot** swap through the router (router not allowlisted → revert) |
| Allowlist the router to support router-mediated swaps | **All** users bypass the per-user gate (router is allowlisted → any user passes) |

There is no configuration that simultaneously enforces per-user access control and supports the standard router entry point.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` for per-user curation (e.g., KYC-only pools, institutional-only pools, or pools restricted to specific counterparties) is completely bypassed by any user who routes through `MetricOmmSimpleRouter`. The allowlist policy — the sole access-control mechanism on the swap path — is rendered ineffective. Unauthorized users can execute swaps against restricted LP positions, directly impacting LP principal through trades that the pool's access policy was designed to prevent. This constitutes a broken core pool functionality causing direct loss of funds to LPs who rely on the allowlist for access control.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who deploys a `SwapAllowlistExtension` and also wants to support the standard router (the expected integration path) will naturally allowlist the router address, inadvertently opening the pool to all users. The bypass requires no special knowledge or privileged access — any user can call `exactInputSingle` on the router. The precondition (router allowlisted) is the natural and expected configuration for any pool that uses both the allowlist extension and the router.

## Recommendation
The pool must forward the original end-user identity through the swap call so extensions can gate on the economically relevant actor. Two approaches:

1. **Add a `payer` parameter to `swap()`**: The pool accepts an explicit `payer` address (verified via callback), and passes it as `sender` to extensions instead of `msg.sender`. The router would pass `msg.sender` (the actual user) as `payer`. This is consistent with how `addLiquidity` already separates `msg.sender` (the payer/sender) from `owner` (the position holder).

2. **Extension-level forwarding via `extensionData`**: Require the router to encode the original caller in `extensionData`, and update `SwapAllowlistExtension` to decode and verify it (with a signature or trusted-forwarder pattern). This is more complex and requires the extension to trust the router.

Option 1 is cleaner and architecturally consistent with the existing liquidity flow.

## Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension as beforeSwap hook
// 2. Pool admin allowlists only `alice` to swap
// 3. Pool admin also allowlists `router` to support router-mediated swaps for alice
// 4. Bob (not allowlisted) calls router.exactInputSingle() → PASSES (router is allowlisted)

function test_swapAllowlist_bypassViaRouter() public {
    // Pool admin allowlists alice directly and the router for router-mediated swaps
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Alice can swap directly (sender = alice ✓)
    vm.prank(alice);
    pool.swap(alice, true, 1000, 0, "", "");

    // Bob (not allowlisted) bypasses the allowlist via the router
    // pool.swap() sees msg.sender = router (allowlisted) → passes
    // extension checks allowedSwapper[pool][router] = true → passes
    vm.prank(bob); // bob is NOT in allowedSwapper
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: bob,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));
    // Bob's swap succeeds — allowlist completely bypassed
}
```
Audit Report

## Title
Router Substitutes Its Own Address for the User's Identity in `SwapAllowlistExtension.beforeSwap`, Enabling Allowlist Bypass - (File: metric-periphery/contracts/MetricOmmSimpleRouter.sol, metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to `SwapAllowlistExtension.beforeSwap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any user who is not individually allowlisted can bypass the swap allowlist by routing through the router if the router address is allowlisted on the pool, and conversely, individually allowlisted users cannot swap through the router at all.

## Finding Description

**Call path:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — the router is `msg.sender` at the pool boundary.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` where `msg.sender` is the router address.
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`.

**Root cause — pool `swap` uses `msg.sender` as `sender`:**
```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // <-- always the direct caller, i.e. the router
  recipient,
  ...
);
```

**Router never forwards the originating user:**
```solidity
// MetricOmmSimpleRouter.sol line 72-80
IMetricOmmPoolActions(params.pool).swap(
  params.recipient,
  params.zeroForOne,
  ...
);  // msg.sender at pool = router, not the end user
```

**Extension checks the wrong identity:**
```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
// msg.sender = pool, sender = router (not the end user)
```

**Exploit path A — bypass by routing:**
- Pool admin allowlists the router address (a natural configuration for pools that want to support router-mediated swaps).
- Any non-allowlisted user calls `router.exactInputSingle(...)`.
- Extension sees `sender = router`, which is allowlisted → swap proceeds.
- The allowlist is fully bypassed for all router users.

**Exploit path B — broken functionality:**
- Pool admin allowlists specific EOAs (e.g., KYC'd users).
- Those users call the router; the router's address is not allowlisted → `NotAllowedToSwap` revert.
- Allowlisted users cannot use the router at all, breaking the intended swap flow.

Existing guards are insufficient: `SwapAllowlistExtension` has no mechanism to unwrap the originating user from the router call, and the pool has no forwarding mechanism for the true initiator.

## Impact Explanation
The `SwapAllowlistExtension` is a core access-control primitive for restricted pools. Its bypass allows unprivileged, non-allowlisted traders to execute swaps on pools that are intended to be gated. This constitutes a broken core pool functionality (the allowlist gate) and an admin-boundary break where an unprivileged path (routing through the public router) circumvents a configured access restriction. Depending on pool design, this can result in unauthorized price impact, unauthorized fee extraction, or unauthorized trading on compliance-restricted pools.

## Likelihood Explanation
The condition is reachable by any unprivileged user with no special privileges. The only precondition is that the pool has `SwapAllowlistExtension` configured and the router address is allowlisted (a natural and expected configuration for pools that support router access). The router is a public, permissionless contract. The attack is repeatable on every swap.

## Recommendation
The `SwapAllowlistExtension` should check the true originating user rather than the direct pool caller. Two options:

1. **Preferred:** Add an `originator` field to the extension data that the router populates with `msg.sender` before calling the pool. The extension reads and verifies this field, and the pool/router enforce that only trusted routers can set it.
2. **Alternative:** The pool's `swap` interface should accept an explicit `sender` parameter (the originating user) that the router passes as `msg.sender`, similar to how `addLiquidity` separates `msg.sender` (payer) from `owner` (position holder). The extension then checks this explicit sender.

## Proof of Concept

```solidity
// Foundry test sketch
function test_routerBypassesSwapAllowlist() public {
    // Setup: pool with SwapAllowlistExtension, router allowlisted
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // attacker is NOT individually allowlisted

    // Attacker routes through the router
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    }));
    // Swap succeeds — allowlist bypassed
}
```
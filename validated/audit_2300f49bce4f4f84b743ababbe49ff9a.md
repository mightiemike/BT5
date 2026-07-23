Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual user, enabling allowlist bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of the pool's `swap()` call — the router contract, not the end user. When a pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the per-pool swap allowlist by routing through `MetricOmmSimpleRouter`. The allowlist's intended identity gate is silently replaced by a router-address gate.

## Finding Description

**Call path:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`).
2. The router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — the router is `msg.sender` to the pool.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` where `msg.sender` is the **router address**.
4. `ExtensionCalling._beforeSwap()` encodes `sender = router_address` and dispatches to the configured extension.
5. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` executes:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
// msg.sender = pool (correct key)
// sender     = router (WRONG identity — should be the end user)
```

The pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`, and the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`.

**Why existing guards fail:**

The `onlyPool` guard in `BaseMetricExtension` correctly validates that `msg.sender` is the pool. However, the `sender` argument — the identity the allowlist is supposed to gate — is whatever called the pool's `swap()`, which is the router, not the end user. There is no mechanism in the extension or the pool to recover the original EOA.

**Exploit flow:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook, intending to restrict swaps to a known set of addresses.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that router-mediated swaps work for allowlisted users.
3. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting that pool.
4. The pool's `_beforeSwap` passes `sender = router` to the extension.
5. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. The non-allowlisted user successfully swaps, bypassing the allowlist entirely.

## Impact Explanation
The swap allowlist is a core access-control feature. When the router is allowlisted (the only way to support router-mediated swaps), the allowlist is completely bypassed for all users — any unprivileged trader can swap on a pool that was intended to be restricted. This breaks the pool's core swap-gating functionality and constitutes a broken core pool functionality / admin-boundary break reachable by an unprivileged trader. Severity: **Medium** (access control bypass; no direct principal loss unless the pool's restricted nature was protecting against specific counterparties or regulatory requirements, in which case impact escalates).

## Likelihood Explanation
Reachable by any unprivileged user with zero special capability. The only precondition is that the pool admin has configured `SwapAllowlistExtension` and allowlisted the router (a routine operational step). The router is a public, permissionless contract. The bypass is repeatable on every swap.

## Recommendation
Pass the original caller's identity through the router to the pool, or have the extension recover it. Two concrete options:

1. **Router encodes payer in `callbackData`/`extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address instead of `sender`.
2. **Pool exposes original caller via transient storage**: The router writes `msg.sender` to a transient slot before calling the pool; the extension reads it. This is consistent with the existing transient-storage callback pattern already used by the router.

Either way, `SwapAllowlistExtension.beforeSwap` must check the **end user's address**, not the intermediary router's address.

## Proof of Concept

```solidity
// Foundry test sketch
function test_swapAllowlist_bypassViaRouter() public {
    // Setup: pool with SwapAllowlistExtension, router allowlisted, attacker not allowlisted
    address attacker = makeAddr("attacker");
    extension.setAllowedToSwap(address(pool), address(router), true);
    // attacker is NOT in allowedSwapper

    // Fund attacker and approve router
    deal(token0, attacker, 1e18);
    vm.prank(attacker);
    IERC20(token0).approve(address(router), type(uint256).max);

    // Attacker swaps through router — should revert NotAllowedToSwap, but does not
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: token0,
        recipient: attacker,
        amountIn: 1000,
        amountOutMinimum: 0,
        zeroForOne: true,
        priceLimitX64: 0,
        deadline: block.timestamp + 1,
        extensionData: ""
    }));
    // Swap succeeds — allowlist bypassed
}
```
Audit Report

## Title
SwapAllowlistExtension checks router address instead of end-user, allowing any unprivileged user to bypass the swap allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged user can bypass the per-user allowlist by routing through the public router.

## Finding Description
**Call path:**

1. Unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`).
2. The router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — inside the pool, `msg.sender` is the **router address**.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, forwarding the **router address** as `sender`.
4. `ExtensionCalling._beforeSwap` encodes and dispatches `IMetricOmmExtensions.beforeSwap(sender=router, ...)` to the extension.
5. `SwapAllowlistExtension.beforeSwap` executes the check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the **router**, not the end user. The check resolves to `allowedSwapper[pool][router]`.

**Root cause:** The pool passes `msg.sender` (the immediate caller of `pool.swap()`) as `sender` to the extension. The extension treats this as the identity to gate. When the router intermediates, the end user's address is never visible to the extension.

**Why existing guards fail:** There is no mechanism in the extension, the pool, or the router to propagate the original `msg.sender` of the router call into the `sender` argument seen by the extension. The `onlyPool` modifier in `BaseMetricExtension` only verifies the pool is the caller of the extension — it does not help recover the true end-user identity.

**Exploit flow:**
- Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists only specific addresses (e.g., KYC'd traders).
- To allow those traders to use the router, the admin must also allowlist the router address: `setAllowedToSwap(pool, router, true)`.
- Once the router is allowlisted, any unprivileged user calls `router.exactInputSingle({pool: curatedPool, ...})`. The extension sees `sender = router`, which is allowlisted, and the swap proceeds.
- The per-user allowlist is completely bypassed.

## Impact Explanation
Any unprivileged user can trade on a curated pool that was intended to restrict swaps to a specific set of addresses. This breaks the core allowlist invariant: "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it." The direct impact is unauthorized swap execution on pools designed for restricted access, which can result in direct loss of LP value if the pool's pricing or liquidity is calibrated for a specific counterparty set, or complete curation failure enabling unrestricted trading on a supposedly gated pool.

## Likelihood Explanation
The attack requires no special privileges. Any user with tokens and approval can call the public `MetricOmmSimpleRouter`. The only precondition is that the pool admin has allowlisted the router (which is the natural operational step to enable router-mediated swaps for legitimate users). The attack is repeatable on every block and requires no timing or oracle manipulation.

## Recommendation
The extension must gate the **originating user**, not the immediate pool caller. Two sound approaches:

1. **Pass the original caller explicitly:** Add an `originator` field to the extension data or a dedicated argument in `beforeSwap`, populated by the router with `msg.sender` before calling the pool. The extension then checks `allowedSwapper[pool][originator]` and verifies the originator field was set by a trusted router.
2. **Gate at the router level:** The router reads the allowlist before calling the pool and reverts if the calling user is not permitted, removing the need for the extension to recover the end-user identity. This requires the router to be aware of the allowlist contract.

## Proof of Concept
```solidity
// Foundry test sketch
function test_allowlistBypassViaRouter() public {
    // Setup: curated pool with SwapAllowlistExtension
    // Only `allowedUser` is individually allowlisted
    swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
    // Admin also allowlists the router so allowedUser can use it
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Attack: unprivilegedUser (NOT on allowlist) routes through the router
    vm.startPrank(unprivilegedUser);
    token0.approve(address(router), type(uint256).max);
    // This should revert but does NOT — router address passes the allowlist check
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        recipient: unprivilegedUser,
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        extensionData: ""
    }));
    vm.stopPrank();
    // unprivilegedUser successfully swapped on a pool they are not allowlisted for
}
```
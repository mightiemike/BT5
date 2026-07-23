Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual end-user, enabling any user to bypass per-user swap restrictions via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When `MetricOmmSimpleRouter` intermediates a swap, `sender` is the router address, not the originating user. A pool admin who allowlists the router to enable router-based swaps inadvertently grants every user who calls through the router the ability to bypass the per-user restriction, making the allowlist unenforceable for router-mediated paths.

## Finding Description
**Root cause — `MetricOmmPool.swap` passes `msg.sender` as `sender`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← this is the router, not the end-user
    ...
```

**`SwapAllowlistExtension.beforeSwap` checks that value against the per-pool allowlist:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
// msg.sender = pool, sender = router address when routed
```

**`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
// pool sees msg.sender = router, not the end-user
```

**Exploit flow:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` and sets `allowAllSwappers[pool] = false` to restrict swaps to specific users.
2. Admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps (a natural operational step).
3. Any unprivileged user — one not individually allowlisted — calls `MetricOmmSimpleRouter.exactInputSingle` targeting that pool.
4. The pool calls `_beforeSwap(msg.sender=router, ...)`, the extension checks `allowedSwapper[pool][router]` which is `true`, and the swap proceeds.
5. The per-user restriction is fully bypassed.

**Why existing guards fail:** There is no mechanism in the router or the pool to forward the originating `msg.sender` (the end-user) to the extension. The extension has no way to distinguish a legitimate allowlisted user from an arbitrary caller when both route through the same router address. The existing unit tests (`SwapAllowlistSubExtension.t.sol`) only test direct pool calls (`vm.prank(address(pool))`), never router-mediated paths, so this gap is untested.

## Impact Explanation
The swap allowlist is a core access-control mechanism. When the router is allowlisted, the restriction is completely nullified for all router callers — any user can trade on a pool that was intended to be restricted to specific counterparties (e.g., KYC'd users, whitelisted institutions). This constitutes broken core pool functionality: the allowlist extension fails to enforce the access boundary it was designed to enforce, allowing unauthorized users to execute swaps against LPs who expected only approved counterparties.

## Likelihood Explanation
The precondition — the router being allowlisted — is a natural and expected operational step for any pool admin who wants to support router-based swaps while also restricting the swapper set. The admin has no way to simultaneously allow router-mediated swaps and enforce per-user restrictions using the current design. Once the router is allowlisted, the bypass is trivially repeatable by any user with no special privileges, no capital requirements beyond the swap amount, and no time constraints.

## Recommendation
The extension should check the actual end-user identity rather than the direct pool caller. Two concrete approaches:

1. **Pass originating caller through `extensionData`:** Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. This requires the router to be trusted to populate this field honestly, which can be enforced by checking that `sender` (the direct caller) is a known trusted router before trusting the decoded user address.

2. **Check `recipient` instead of `sender`:** If the pool's intended design is that the recipient is always the economic beneficiary, gate on `recipient`. However, this changes the semantics of the allowlist.

3. **Structural fix:** Add a `trustedRouter` registry to the extension. When `sender` is a trusted router, require the extension to receive the real user address via `extensionData` (signed or encoded by the router), and check that address against `allowedSwapper`.

## Proof of Concept
```solidity
// Foundry test sketch
function test_routerBypassesSwapAllowlist() public {
    // Setup: pool with SwapAllowlistExtension, router allowlisted, attacker not allowlisted
    address attacker = makeAddr("attacker");
    vm.prank(admin);
    extension.setAllowedToSwap(pool, address(router), true);
    // attacker is NOT individually allowlisted

    // Attacker calls through router — should revert but does not
    vm.prank(attacker);
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    }));
    // swap succeeds — allowlist bypassed
}
```
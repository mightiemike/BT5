Now I have all the information needed to trace the complete call chain. Let me verify the test helper to confirm the direct-call vs router-call identity difference.

Audit Report

## Title
SwapAllowlistExtension checks router address instead of actual user — allowlist bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's address against the allowlist instead of the actual user's address. Any non-allowlisted user can therefore bypass a per-user swap allowlist on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point), provided the router address itself is allowlisted.

## Finding Description

**Call chain — direct pool swap (correct):**
1. User EOA → `pool.swap()` — pool sees `msg.sender = user`
2. Pool calls `_beforeSwap(msg.sender=user, ...)` in `ExtensionCalling._beforeSwap`
3. Extension receives `sender = user`; check is `allowedSwapper[pool][user]` ✓

**Call chain — router-mediated swap (broken):**
1. User EOA → `MetricOmmSimpleRouter.exactInputSingle()` — router sees `msg.sender = user`
2. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`
3. Pool calls `_beforeSwap(msg.sender=router, ...)` in `ExtensionCalling._beforeSwap`
4. Extension receives `sender = router`; check is `allowedSwapper[pool][router]` ✗

**Root cause — `MetricOmmPool.swap` passes `msg.sender` as `sender`:**

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

**Root cause — `SwapAllowlistExtension.beforeSwap` checks that `sender` argument:**

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When the router calls `pool.swap()`, `sender` is the router address. If the router is allowlisted (which it must be for any legitimate user to use it), every user — including non-allowlisted ones — passes the check.

The existing test `test_allowedSwapSucceeds` in `FullMetricExtensionTest` confirms the design: it allowlists `callers[0]` (the direct pool-calling contract), not `users[0]` (the EOA). There is no test covering the router path against an allowlisted pool, and no mechanism in the router to forward the original `msg.sender` to the pool.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC'd counterparties, whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The allowlist policy is silently bypassed: unauthorized users can execute swaps, receive output tokens, and drain pool liquidity at oracle-derived prices. This is a direct loss of the curation guarantee and constitutes broken core pool functionality (allowlist gate fails open on the supported periphery path).

## Likelihood Explanation
The `MetricOmmSimpleRouter` is the primary public swap interface documented and expected to be used by integrators. Any user who knows the pool has a swap allowlist can trivially bypass it by calling the router instead of the pool directly. No special privileges, flash loans, or multi-transaction setup are required — a single `exactInputSingle` call suffices. The bypass is repeatable on every block.

## Recommendation
The router must forward the original caller's identity to the pool so the extension can gate the correct actor. Two viable approaches:

1. **Pass the original sender in `callbackData`**: The pool could expose a mechanism for the router to declare the originating user, and the extension could read it from `extensionData`. This requires a protocol-level convention.
2. **Check `extensionData` in the extension**: The router encodes `msg.sender` into `extensionData`; the extension reads and verifies it. This requires the extension to trust the router's encoding, which reintroduces a trust assumption.
3. **Preferred — allowlist at the router level**: The `SwapAllowlistExtension` should be redesigned to accept an explicit `originator` field passed through `extensionData` by the router, with the router encoding `msg.sender` before forwarding to the pool. The extension then checks `allowedSwapper[pool][originator]` and also verifies the call came from a trusted router.

## Proof of Concept

```solidity
// Foundry test demonstrating the bypass
function test_swapAllowlist_bypassViaRouter() public {
    // Pool admin allowlists only the router (required for any router user to swap)
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Non-allowlisted attacker calls the router — extension sees sender=router, passes
    address attacker = makeAddr("attacker");
    token0.mint(attacker, 1_000_000e18);
    vm.prank(attacker);
    token0.approve(address(router), type(uint256).max);

    vm.prank(attacker);
    uint256 amountOut = router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token0),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1000,
            amountOutMinimum: 0,
            recipient: attacker,
            deadline: type(uint256).max,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // Attacker receives output despite not being individually allowlisted
    assertGt(amountOut, 0);
}
```
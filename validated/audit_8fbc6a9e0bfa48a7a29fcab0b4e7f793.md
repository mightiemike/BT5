Audit Report

## Title
Swap Allowlist Bypass via Router: `sender` Bound to Router Address Instead of Original EOA - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original EOA. If the pool admin allowlists the router address to permit router-mediated swaps for any user, every unprivileged user can bypass the per-user allowlist by calling through the router, defeating the curation guarantee entirely.

## Finding Description

**Call path — direct swap (correct):**
1. EOA → `MetricOmmPool.swap(...)` — pool sees `msg.sender = EOA`
2. Pool calls `_beforeSwap(msg.sender=EOA, ...)` (`ExtensionCalling.sol:231`)
3. `SwapAllowlistExtension.beforeSwap(sender=EOA, ...)` checks `allowedSwapper[pool][EOA]` ✓

**Call path — router swap (broken):**
1. EOA → `MetricOmmSimpleRouter.exactInputSingle(params)` (`MetricOmmSimpleRouter.sol:67-86`)
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — pool sees `msg.sender = router`
3. Pool calls `_beforeSwap(msg.sender=router, ...)` (`MetricOmmPool.sol:230-240`)
4. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router]`

The `sender` argument forwarded to the extension is always `msg.sender` of the pool's `swap()` entry point (`MetricOmmPool.sol:231`). The router never forwards the original EOA identity; it is structurally impossible for the extension to recover it.

**Exploit flow:**
- Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists specific EOAs.
- To allow any allowlisted user to use the router, the admin must call `setAllowedToSwap(pool, router, true)`.
- Once the router is allowlisted, **any** EOA — including non-allowlisted ones — calls `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` through the router and the extension sees `sender = router`, which passes the check.
- The per-user allowlist is completely bypassed.

**Existing guards are insufficient:** `SwapAllowlistExtension.beforeSwap` (`SwapAllowlistExtension.sol:37`) only inspects the `sender` argument, which is `msg.sender` of the pool call. There is no mechanism in the extension, the pool, or the router to thread the original EOA through to the hook. The router stores the original payer in transient storage (`TransientCallbackPool`, slot `T_PAYER_SLOT`) for payment settlement only; this value is never surfaced to extensions.

## Impact Explanation
A curated pool whose swap allowlist is intended to restrict trading to KYC'd or permissioned addresses can be bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The wrong value is `sender` in `allowedSwapper[pool][sender]`: it resolves to the router address instead of the originating EOA. This constitutes a direct policy bypass on curated pools — unauthorized users execute swaps that the allowlist was designed to block, which can drain LP value or violate regulatory/curation requirements. Severity: High.

## Likelihood Explanation
The bypass is trivially reachable by any unprivileged user: call any of the four public router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) targeting an allowlisted pool. The only precondition is that the router address has been added to the allowlist (which is required for any legitimate user to use the router). No special privileges, flash loans, or multi-block setup are needed. The attack is repeatable every block.

## Recommendation
The extension must gate on the economically relevant actor, not the immediate pool caller. Two sound approaches:

1. **Pass original initiator through the pool:** Add an `initiator` field to the swap call or extension data that the router populates with `msg.sender` before calling the pool, and have the extension verify that field instead of (or in addition to) `sender`.
2. **Gate on `recipient` or a signed credential:** Require the allowlisted actor to be the `recipient` of the swap output, or require the `extensionData` to carry a signed proof of the original EOA's identity that the extension verifies on-chain.
3. **Short-term mitigation:** Document that allowlisting the router address opens the pool to all users, and provide a separate per-user router allowlist that the router enforces before calling the pool.

## Proof of Concept

```solidity
// Foundry test sketch
function test_swapAllowlistBypassViaRouter() public {
    // Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted
    swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
    // Admin must also allowlist the router so allowedUser can use it
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Attack: attacker (not in allowlist) routes through the router
    vm.startPrank(attacker); // attacker is NOT in allowedSwapper
    token1.approve(address(router), type(uint256).max);
    // This succeeds because extension sees sender=router, which IS allowlisted
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token1),
        tokenOut: address(token0),
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: attacker,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp + 1,
        extensionData: ""
    }));
    vm.stopPrank();
    // Attacker successfully swapped despite not being in the allowlist
}
```
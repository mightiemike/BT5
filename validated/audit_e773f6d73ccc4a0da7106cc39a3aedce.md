I have now read all the relevant files. Let me trace the full call path carefully before rendering a verdict.

Audit Report

## Title
`SwapAllowlistExtension` receives router address as `sender`, allowing any user to bypass per-user swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
When a user swaps through `MetricOmmSimpleRouter`, the pool passes `msg.sender` (the router address) as the `sender` argument to all extension hooks. `SwapAllowlistExtension.beforeSwap()` gates access by checking `allowedSwapper[pool][sender]`, where `sender` is the router — not the actual user. If the router is allowlisted (required for any router-based swap to succeed), every user can bypass the per-user allowlist by routing through the router, rendering the extension's access control ineffective.

## Finding Description

**Call path:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput` / `exactOutputSingle` / `exactOutput`).
2. Router calls `IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — `msg.sender` of this call is the router.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)` where `msg.sender` = router address.
4. `ExtensionCalling._beforeSwap()` ABI-encodes `sender = router` and calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
5. `SwapAllowlistExtension.beforeSwap()` evaluates `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`.

**Root cause:**

`MetricOmmPool.swap()` unconditionally passes `msg.sender` as `sender` to extensions:

```solidity
// MetricOmmPool.sol line 231
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap(), i.e. the router
    recipient, ...
);
```

`SwapAllowlistExtension.beforeSwap()` checks this value:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Why existing guards are insufficient:**

- `_requireFactoryPool(pool)` only verifies the pool is factory-registered; it does not propagate the original user's identity.
- `_requireExpectedCallbackCaller(msg.sender)` guards the payment callback, not the swap initiator identity.
- No mechanism in the router passes the actual `msg.sender` (the user) to the pool or extension.

**Exploit flow:**

1. Pool admin deploys pool with `SwapAllowlistExtension` to restrict swaps to specific addresses.
2. Pool admin must allowlist the router address to enable any router-based swaps: `setAllowedToSwap(pool, router, true)`.
3. Pool admin does NOT allowlist user B.
4. User B calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` — extension checks `allowedSwapper[pool][router]` = `true` → swap succeeds.
6. User B bypassed the per-user allowlist entirely.

Note: `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (second parameter), which is explicitly supplied by the caller and correctly identifies the depositor regardless of intermediary.

## Impact Explanation
The `SwapAllowlistExtension` is the protocol's mechanism for pools to enforce per-user swap access control (e.g., KYC/compliance gating, institutional-only pools). When users interact through the router — the primary intended user-facing entry point — the allowlist check collapses to a single router-level check. Any user can bypass the per-user restriction by calling the router, making the extension's per-address gating completely ineffective for router-routed swaps. This breaks the allowlist path invariant explicitly identified as a Smart Audit Pivot: "deposit/swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through router."

## Likelihood Explanation
Likelihood is high. Any unprivileged user can call `MetricOmmSimpleRouter` directly with no special preconditions. The only prerequisite — the router being allowlisted — is a necessary operational condition for the router to function at all with allowlist-gated pools. A pool admin who enables the router for their allowlisted pool inadvertently opens it to all users.

## Recommendation
Pass the original user's address through the extension data or as a dedicated parameter. One approach: the router encodes `msg.sender` into `extensionData` before calling `pool.swap()`, and `SwapAllowlistExtension` decodes and checks it. Alternatively, the pool interface could be extended with an explicit `initiator` parameter distinct from `sender`. A simpler mitigation is to document that `SwapAllowlistExtension` cannot enforce per-user restrictions when a router intermediary is used, and provide a separate extension that reads the initiator from `extensionData`.

## Proof of Concept

```solidity
// Foundry test sketch
function test_swapAllowlistBypassViaRouter() public {
    // 1. Deploy pool with SwapAllowlistExtension
    // 2. Pool admin allowlists router (required for router swaps)
    swapAllowlist.setAllowedToSwap(pool, address(router), true);
    // 3. User B is NOT allowlisted
    assertFalse(swapAllowlist.isAllowedToSwap(pool, userB));
    // 4. User B swaps through router — succeeds despite not being allowlisted
    vm.prank(userB);
    router.exactInputSingle(ExactInputSingleParams({
        pool: pool,
        tokenIn: token0,
        recipient: userB,
        amountIn: 1e18,
        amountOutMinimum: 0,
        zeroForOne: true,
        priceLimitX64: 0,
        deadline: block.timestamp + 1,
        extensionData: ""
    }));
    // swap succeeds — allowlist bypassed
}
```
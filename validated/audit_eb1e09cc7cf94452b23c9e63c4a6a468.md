Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` as the direct `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the originating user. If the router is allowlisted (required for normal router-mediated swaps), any unprivileged user can bypass the per-pool swap allowlist by calling through the router.

## Finding Description

**Root cause — sender is the immediate caller of `swap()`, not the originating user.**

In `MetricOmmPool.swap()`, `msg.sender` is captured and forwarded to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← direct caller of swap()
    recipient, zeroForOne, amountSpecified,
    priceLimitX64, packedSlot0Initial,
    bidPriceX64, askPriceX64, extensionData
);
```

`ExtensionCalling._beforeSwap` passes this value as `sender` to every configured extension:

```solidity
// ExtensionCalling.sol:162-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

**Exploit path:**

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64, "", params.extensionData
);
```

At this point `msg.sender` inside `pool.swap()` is the **router**, not the originating EOA. The extension therefore checks `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (the only way to permit any router-mediated swap), every user — including those explicitly not in the allowlist — passes the gate. The router carries no per-user identity into the extension.

**Existing guards are insufficient:** The router validates only that the callback caller is a factory pool (`_requireExpectedCallbackCaller`). It does not forward the originating user's address to the pool or to extensions. There is no mechanism in the current extension interface for the pool to recover the original EOA from the router context.

## Impact Explanation
The swap allowlist is a core access-control feature. Its failure allows any unprivileged user to execute swaps on a pool that the admin intended to restrict to a specific set of addresses. This constitutes broken core pool functionality (allowlist gate bypassed) and an admin-boundary break: the pool admin's explicit per-address restriction is circumvented by any user who routes through the public router. Pools relying on the allowlist for compliance, private liquidity, or risk controls are fully exposed.

## Likelihood Explanation
The router is a public, permissionless contract. Any user can call `exactInputSingle` or `exactInput` targeting an allowlist-gated pool. The only precondition is that the pool admin has allowlisted the router (which is the normal operational requirement to support router-mediated swaps at all). No special privileges, flash loans, or unusual conditions are required. The bypass is repeatable on every swap.

## Recommendation
The extension interface must receive the originating user's address separately from the immediate `msg.sender`. One approach: add an `originator` field to the `beforeSwap` hook arguments that the router populates (e.g., via `extensionData` or a dedicated transient slot read by the pool before dispatching hooks). `SwapAllowlistExtension` should then gate on `originator` when the immediate sender is a known router, or the pool should always pass the true EOA through a transient context set by the router before calling `swap()`. Alternatively, require that allowlisted routers implement an interface that exposes the originating user, and have the extension query it.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlisting the router so router-mediated swaps work for permitted users.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. Attacker (non-allowlisted EOA) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Attacker successfully swaps on a pool they were explicitly excluded from.

Foundry test skeleton:
```solidity
function test_swapAllowlistBypassViaRouter() public {
    // pool configured with SwapAllowlistExtension
    // allowedSwapper[pool][address(router)] = true
    // allowedSwapper[pool][attacker] = false (never set)
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
    // expect: swap succeeds — allowlist bypassed
}
```
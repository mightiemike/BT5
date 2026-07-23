Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct `msg.sender` of `MetricOmmPool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender` equals the router address, not the end-user. A pool admin who allowlists the router to enable router-based swaps inadvertently grants unrestricted swap access to every caller of the router, completely defeating the per-user allowlist.

## Finding Description

**Call path establishing the wrong identity:**

In `MetricOmmPool.swap()`, the pool dispatches the before-swap hook with `msg.sender` as the `sender` argument:

```solidity
_beforeSwap(
    msg.sender,   // sender = direct caller of pool.swap()
    recipient,
    ...
);
```
(`metric-core/contracts/MetricOmmPool.sol`, line 231)

`ExtensionCalling._beforeSwap` forwards this verbatim to every configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))` (`metric-core/contracts/ExtensionCalling.sol`, lines 160–176).

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
(`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`, line 37)

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

**Router path:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```
(`metric-periphery/contracts/MetricOmmSimpleRouter.sol`, lines 72–80)

So when an end-user calls `exactInputSingle`, the router becomes `msg.sender` of `pool.swap()`, making `sender = router` in `beforeSwap`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Bypass path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured, intending to restrict swaps to specific addresses.
2. Admin allowlists the router: `setAllowedToSwap(pool, router, true)` so that legitimate router-based swaps work.
3. Any unprivileged user — including addresses the admin never intended to permit — calls `MetricOmmSimpleRouter.exactInputSingle` targeting that pool.
4. `beforeSwap` receives `sender = router`, checks `allowedSwapper[pool][router] == true`, and passes. The swap executes.
5. The end-user's address is never checked. There is no field in the `beforeSwap` signature that carries the original end-user's address, and no on-chain mechanism to enforce it.

**Existing guards are insufficient:** The only check in `beforeSwap` is `allowedSwapper[msg.sender][sender]`. There is no secondary check on the original transaction originator (`tx.origin` is not used, nor is any user-identity field in `extensionData` enforced). The `BaseMetricExtension.onlyPool` modifier only ensures the caller is a registered pool — it does not help identify the end-user.

## Impact Explanation
The swap allowlist is a core access-control feature. Its bypass allows any unprivileged user to trade in a pool the admin intended to restrict. Depending on the pool's purpose (e.g., institutional-only, KYC-gated, whitelist-only liquidity), this enables unauthorized parties to extract value from LP positions at oracle-anchored prices, constituting broken core pool functionality and an admin-boundary break. LP funds are directly at risk if the pool was designed to serve only trusted counterparties.

## Likelihood Explanation
The bypass requires only that the pool admin has allowlisted the router — a necessary step for any pool that intends to support router-based swaps. Any user with knowledge of the pool address and the router can exploit this immediately and repeatably with no special privileges, capital, or timing constraints. The router is a public, permissionless contract.

## Recommendation
The extension must gate on the true end-user identity, not the intermediary. Options:

1. **Pass originator through `extensionData`:** Require the router to encode `msg.sender` (the end-user) into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it. This requires the pool admin to trust that the router correctly populates this field.
2. **Check `tx.origin` as a fallback:** When `sender` is a known router, fall back to `tx.origin`. This is fragile and incompatible with smart-contract wallets.
3. **Preferred — router-level allowlist enforcement:** Add a separate allowlist check in the router itself before calling `pool.swap()`, and document that `SwapAllowlistExtension` is incompatible with router-mediated swaps unless the router enforces its own per-user gate.
4. **Structural fix:** Extend the `beforeSwap` hook signature to include an `originator` field populated by the pool from a trusted transient-storage context set by the router, similar to how the router already uses transient storage for callback context (`_setNextCallbackContext`).

## Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext as beforeSwap extension
// Admin allowlists only alice directly:
ext.setAllowedToSwap(pool, alice, true);
// Admin also allowlists the router to support router-based swaps:
ext.setAllowedToSwap(pool, address(router), true);

// Attack: bob (not allowlisted) swaps via router
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    deadline: block.timestamp,
    extensionData: ""
}));
// beforeSwap receives sender=router, checks allowedSwapper[pool][router]=true → passes
// Bob's swap succeeds despite never being allowlisted
```
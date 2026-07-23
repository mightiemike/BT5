Audit Report

## Title
`SwapAllowlistExtension` checks the immediate pool caller (router) instead of the originating user, enabling allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` as the address that called `MetricOmmPool.swap` (i.e., `msg.sender` inside the pool). When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to permit router-mediated swaps for legitimate users, every non-allowlisted user can bypass the per-user allowlist by routing through the same public router contract.

## Finding Description
**Root cause:** `MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- always the immediate caller of the pool
  recipient,
  ...
  extensionData
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension via `abi.encodeCall`. `SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `IMetricOmmPoolActions(params.pool).swap(...)` directly — making the router the pool's `msg.sender`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Exploit flow:**
1. Pool admin deploys pool with `SwapAllowlistExtension` to restrict swaps to KYC'd/whitelisted users.
2. Admin allowlists `alice` (legitimate user): `setAllowedToSwap(pool, alice, true)`.
3. Admin also allowlists the router so alice can use router convenience features: `setAllowedToSwap(pool, address(router), true)`.
4. `bob` (not allowlisted) calls `router.exactInputSingle(...)` targeting the curated pool.
5. Router calls `pool.swap(...)` — pool's `msg.sender` = router.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap succeeds.
7. Bob has swapped on a pool he was never authorized to access.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from using the same router. The admin faces an irresolvable dilemma: allowlist the router (bypass enabled) or don't (allowlisted users lose router access).

## Impact Explanation
A curated pool using `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, whitelisted market makers) is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. The LP assets of the curated pool are exposed to unauthorized swaps, enabling adverse selection and direct loss of LP principal. This is a broken core pool functionality/admin-boundary break causing direct loss of LP funds — the extension decision value (`allowedSwapper[pool][sender]`) is bound to the wrong actor (router instead of end user), defeating the entire purpose of the allowlist.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` targeting the curated pool with no special privilege or setup beyond knowing the pool address. The pool admin is likely to allowlist the router because without it, allowlisted users lose access to all router convenience features (multi-hop, exact-output, slippage protection). The bypass requires zero exploit sophistication.

## Recommendation
The extension must resolve the end-user identity rather than trusting the `sender` parameter when `sender` is a known router. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData` before calling the pool. The extension decodes and checks that address. This requires a trusted encoding convention between the router and extension.

2. **Dual-check with router-reported origin**: The extension checks `allowedSwapper[pool][sender]` first; if `sender` is a registered router, it additionally checks a user-identity field the router supplies in `extensionData`.

The simplest safe interim measure is to document and enforce in code that the router must never be allowlisted and that allowlisted users must call the pool directly — but this must be enforced via a contract-level guard (e.g., revert if `sender` is a known router contract), not documentation alone.

## Proof of Concept
```solidity
// Setup
swapAllowlist.setAllowedToSwap(pool, alice, true);
swapAllowlist.setAllowedToSwap(pool, address(router), true); // needed for alice to use router

// Attack: bob (not allowlisted) bypasses via router
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: token0,
        recipient: bob,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Swap succeeds: extension saw sender=router, router is allowlisted
// Bob swapped on a pool he was never authorized to access
```

The extension receives `sender = address(router)` (confirmed by `MetricOmmPool.swap` passing `msg.sender` at L231 and `MetricOmmSimpleRouter.exactInputSingle` calling `pool.swap` directly at L72-80), `allowedSwapper[pool][router]` returns `true`, and the guard passes — even though `bob` is not allowlisted.
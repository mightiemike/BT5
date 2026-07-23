Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the immediate pool caller (router) instead of the original user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` — the router contract when a user routes through `MetricOmmSimpleRouter`. Any pool admin who allowlists the router (the only way to enable router-based swaps for allowlisted users) simultaneously grants every non-allowlisted user the ability to bypass the curated pool's access control by routing through the public periphery.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` is the pool (the extension's caller). `sender` is the first argument, which `MetricOmmPool.swap` sets to its own `msg.sender`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // becomes `sender` in the extension
```

`ExtensionCalling._beforeSwap` passes this value unchanged into `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`.

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, not the original user:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][original_user]`. A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. Once the router is allowlisted, any user — including non-allowlisted ones — can bypass the restriction by routing through the public `MetricOmmSimpleRouter`.

`DepositAllowlistExtension` does not share this flaw: its `beforeAddLiquidity` hook checks `owner` (the second argument), which is the position recipient explicitly supplied by the caller and preserved correctly through the `MetricOmmPoolLiquidityAdder` call chain.

## Impact Explanation
This is a direct admin-boundary break. The pool admin configures `SwapAllowlistExtension` to restrict swap access to a curated set of addresses (e.g., KYC-verified users, institutional counterparties, RWA-compliant addresses). Any non-allowlisted user can circumvent this restriction by routing through the public `MetricOmmSimpleRouter`, executing swaps on the curated pool without authorization. Depending on the pool's purpose, this enables unauthorized fund flows through a pool whose access control has been rendered ineffective.

## Likelihood Explanation
Medium. The bypass requires the router to be allowlisted on the pool. This is a natural and expected configuration for any curated pool that intends to support router-based swaps for its allowlisted users — there is no other mechanism to enable router access. The router is a public, documented periphery contract callable by any address. The condition is therefore likely to be met in production deployments of `SwapAllowlistExtension`-protected pools.

## Recommendation
The extension must verify the original initiating user, not the immediate pool caller. Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` into `extensionData` for each hop and have the extension decode and verify it. The pool admin would configure the router as a trusted forwarder, and the extension would check the decoded initiator when `msg.sender` (the pool's caller) is the trusted router.
2. **Separate sender/initiator fields**: Extend the `beforeSwap` hook signature to carry both the immediate caller and the original initiator, with the pool or router responsible for populating the initiator field.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` attached.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowlisted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted to enable router-based swaps for Alice.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
6. Pool calls `_beforeSwap(router, ...)` → `ExtensionCalling` encodes and calls `extension.beforeSwap(router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes successfully on the curated pool despite not being allowlisted.

Foundry test plan: deploy `SwapAllowlistExtension`, attach to a pool, configure as above, call `exactInputSingle` from an address not in the allowlist, assert the swap succeeds (no `NotAllowedToSwap` revert).
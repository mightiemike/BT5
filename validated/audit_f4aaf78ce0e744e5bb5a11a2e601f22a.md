Audit Report

## Title
SwapAllowlistExtension allowlist bypass via MetricOmmSimpleRouter router identity substitution - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter against a per-pool allowlist, but `sender` is `msg.sender` of `pool.swap()` — the immediate caller — not the end user. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract. Any pool admin who allowlists the router to enable standard-periphery access for permitted users simultaneously grants unrestricted swap access to every unpermitted user, completely defeating the allowlist.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // immediate caller of pool.swap()
  ...
```

`ExtensionCalling._beforeSwap` forwards this value as `sender` to the extension:

```solidity
// ExtensionCalling.sol L162-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
```

`SwapAllowlistExtension.beforeSwap` checks `sender` against the allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is used, it calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
  );
```

The pool sees `msg.sender = router`. The extension evaluates `allowedSwapper[pool][router]`. There is no mechanism in the router to forward the original `msg.sender` (the end user) into `extensionData` or any other field. The same pattern applies to `exactOutputSingle` (L136-137) and `exactInput` (L104-112).

`DepositAllowlistExtension` does not share this flaw because it checks `owner` (L38: `allowedDepositor[msg.sender][owner]`), which is the explicit position owner passed by the caller — a value that cannot be substituted by an intermediary router without the position owner's cooperation.

## Impact Explanation
Any unpermitted user can swap on a curated pool configured with `SwapAllowlistExtension` provided the pool admin has allowlisted the router — the natural and expected operational action to enable standard-periphery access for permitted users. The allowlist is the sole access-control mechanism for such pools. A complete bypass allows disallowed counterparties to drain LP assets from pools intended to be restricted, constituting direct loss of LP principal and a broken core pool invariant (access-controlled swap). This meets the "Broken core pool functionality causing loss of funds" and "Admin-boundary break" impact criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical periphery entry point. Any pool admin deploying a curated pool with `SwapAllowlistExtension` who also wants permitted users to use the standard router will allowlist the router — this is the expected operational pattern. The bypass requires no special privileges: any EOA or contract can call `exactInputSingle` on the router. The condition (router allowlisted) is the common case for any pool that intends to support router-based swaps.

## Recommendation
The extension must verify the actual end user, not the immediate pool caller. The cleanest fix is to have the router encode `msg.sender` into `extensionData` for allowlist-aware pools, and have the extension decode and verify it. Concretely:

1. Add an `originator` field (ABI-encoded `msg.sender`) to `extensionData` in each router swap entry point before calling `pool.swap()`.
2. In `SwapAllowlistExtension.beforeSwap`, decode `originator` from `extensionData` and check `allowedSwapper[pool][originator]` instead of `allowedSwapper[pool][sender]`.

This requires coordinated changes to `MetricOmmSimpleRouter` and `SwapAllowlistExtension`. Alternatively, the extension could also check `allowedSwapper[pool][sender]` as a fallback for direct (non-router) callers, while requiring the decoded originator for router-mediated calls.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use `MetricOmmSimpleRouter`.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` — pool sees `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes on the curated pool despite never being allowlisted.

Foundry test outline:
```solidity
function test_swapAllowlistBypass() public {
    // Setup: pool with SwapAllowlistExtension, alice allowlisted, router allowlisted
    swapAllowlist.setAllowedToSwap(pool, alice, true);
    swapAllowlist.setAllowedToSwap(pool, address(router), true);

    // Bob (not allowlisted) swaps via router — should revert but does not
    vm.prank(bob);
    router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
    // Bob's swap succeeds — allowlist bypassed
}
```
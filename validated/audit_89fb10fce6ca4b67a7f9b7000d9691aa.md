Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address as the swapper identity, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is the direct caller, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. Any user who calls through an allowlisted router bypasses the per-user swap gate entirely, nullifying the pool's curation policy.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L149-177
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router is the direct caller of `pool.swap()`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

So the extension receives `sender = address(router)` and evaluates `allowedSwapper[pool][router]`. The actual end user's identity (`msg.sender` of `exactInputSingle`) is never checked. The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` from the router's address.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` as a `beforeSwap` hook intends to restrict swaps to a curated set of addresses. Once the pool admin allowlists the router (to enable standard periphery access), the allowlist is effectively nullified: every user who calls through the router is seen as the router by the extension, and the router is allowlisted. Non-allowlisted users can execute live swaps on a curated pool, directly violating the pool's curation policy. This constitutes broken core pool functionality and an admin-boundary break via an unprivileged path — both allowed impacts under the contest gate.

## Likelihood Explanation
The trigger requires only that the pool admin has allowlisted the router — a necessary operational step for any pool that intends to support the standard periphery alongside per-user restrictions. Any user can then call `exactInputSingle` or any other router entry point with no special privilege, flash loan, or multi-step setup. The attacker simply calls the router with a valid swap.

## Recommendation
The extension must gate on the actual end user, not the direct pool caller. The cleanest fix is for the router to encode `msg.sender` into `extensionData` and for the extension to decode and verify it. Alternatively, move the allowlist check into the pool's core `swap()` path so it always sees the true `msg.sender`, regardless of the extension system. A signed-permit pattern where the end user's identity is cryptographically bound to the swap call is the most robust option.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: attacker,
        ...
    })
  - Router calls pool.swap(recipient=attacker, ...)
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true  ✓
  - Swap executes; attacker is not individually allowlisted but swaps successfully.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds

Foundry test outline:
  1. Deploy SwapAllowlistExtension and a pool with it as beforeSwap hook.
  2. Deploy MetricOmmSimpleRouter.
  3. Pool admin calls setAllowedToSwap(pool, address(router), true).
  4. Prank as attacker (not individually allowlisted).
  5. Call router.exactInputSingle(...) — assert it succeeds.
  6. Call pool.swap(...) directly as attacker — assert it reverts NotAllowedToSwap.
  Step 5 succeeding while step 6 reverts demonstrates the bypass.
```
Audit Report

## Title
Router-Mediated Swap Passes Router Address as `sender` to `SwapAllowlistExtension::beforeSwap`, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `MetricOmmPool::swap`. When a user swaps through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks whether the router is allowlisted rather than the originating user. Any pool that allowlists the router to support router-mediated swaps for its curated users simultaneously opens the gate to every unprivileged user, completely defeating the per-user allowlist.

## Finding Description
In `MetricOmmPool::swap`, the pool passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← immediate caller, not original user
  recipient,
  ...
);
```

`ExtensionCalling::_beforeSwap` forwards that value unchanged to the extension. `SwapAllowlistExtension::beforeSwap` then evaluates:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter::exactInputSingle`, the router calls `pool.swap()` directly with no mechanism to forward the original user's identity:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The result is that `allowedSwapper[pool][router]` is checked instead of `allowedSwapper[pool][user]`. A pool admin who wants to permit router-mediated swaps for their allowlisted users has no option other than allowlisting the router address. Once the router is allowlisted, the check passes for every caller regardless of whether they appear in the per-user allowlist.

## Impact Explanation
The `SwapAllowlistExtension` is a core access-control hook whose stated purpose is to restrict swaps to a curated set of addresses per pool. Allowlisting the router — the only way to make the extension compatible with the official periphery — silently grants swap access to all users. The invariant "only allowlisted addresses may swap in a curated pool" is broken for every pool that uses both the extension and the router. This constitutes broken core pool functionality: the access-control mechanism that pool admins rely on to restrict trading is rendered ineffective through a supported, public entrypoint.

## Likelihood Explanation
The precondition is that a pool configures `SwapAllowlistExtension` as a `beforeSwap` hook and allowlists the router. This is not an edge case — it is the only way to make the allowlist work with the router at all. Any unprivileged user can then call `MetricOmmSimpleRouter::exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the pool and bypass the per-user check. No special privileges, flash loans, or unusual conditions are required. The attack is repeatable and deterministic.

## Recommendation
The extension must receive the original user's identity rather than the immediate pool caller. The cleanest fix is to have the router write `msg.sender` into transient storage before calling `pool.swap()` (the router already uses transient storage for callback context), and have `SwapAllowlistExtension::beforeSwap` read it back via a known router interface when `msg.sender` (the pool's caller, i.e., the extension's `msg.sender` is the pool, but the pool's caller is the router) is a recognized router. Alternatively, require the router to encode the original sender in `extensionData` and have the extension decode and verify it, rejecting calls where the encoded sender is not allowlisted.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is intended to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — required to allow Alice to use the router.
4. Bob (not in the allowlist: `allowedSwapper[pool][bob] == false`) calls `MetricOmmSimpleRouter::exactInputSingle(...)` targeting the pool.
5. The pool calls `_beforeSwap(msg.sender=router, ...)`.
6. `SwapAllowlistExtension::beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
7. Bob's swap succeeds despite `allowedSwapper[pool][bob] == false`.
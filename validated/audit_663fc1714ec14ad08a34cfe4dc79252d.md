Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, making per-user allowlist enforcement impossible for router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`, `metric-core/contracts/MetricOmmPool.sol`)

## Summary
`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it to `SwapAllowlistExtension.beforeSwap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool boundary is the router contract, not the originating user. `SwapAllowlistExtension` then evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, making it impossible to simultaneously enforce per-user allowlist policy and support router-mediated trading.

## Finding Description
In `MetricOmmPool.swap()`, `msg.sender` is passed directly as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← router address when called via MetricOmmSimpleRouter
  recipient,
  ...
);
```

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly with no originator forwarding:

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

`SwapAllowlistExtension.beforeSwap` then evaluates the check against the router address:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. This produces two mutually exclusive failure modes with no correct configuration:

- **Mode A**: Admin allowlists user addresses but not the router → all router-mediated swaps revert, even for allowlisted users.
- **Mode B**: Admin adds the router to the allowlist to fix Mode A → `allowedSwapper[pool][router] = true` causes every caller (including explicitly blocked addresses) to pass the check when routing through `MetricOmmSimpleRouter`.

The same identity mismatch propagates to `_afterSwap` at `MetricOmmPool.sol` L281-295.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set (KYC-verified addresses, whitelisted institutions, specific market makers) loses that restriction entirely once the router is added to the allowlist. Any unprivileged user can execute swaps on the restricted pool by routing through `MetricOmmSimpleRouter`, receiving output tokens at the pool's oracle-anchored price. This is a direct bypass of the pool's core access-control invariant — broken core pool functionality with direct fund-flow consequences (disallowed parties drain liquidity at pool prices). Severity: High.

## Likelihood Explanation
The trigger is fully unprivileged: any user can call `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()`. The only precondition is that the pool admin has added the router to the allowlist — a natural and expected administrative action for any pool that intends to support router-mediated trading for its allowlisted users. No special permissions, flash loans, or oracle manipulation are required.

## Recommendation
The pool's `swap()` function should accept an explicit `originator` parameter that the router populates with `msg.sender` before calling the pool, and `ExtensionCalling._beforeSwap` should forward that value as `sender` to extensions. Alternatively, `SwapAllowlistExtension.beforeSwap` should decode the original user from `extensionData` when the immediate caller is a known periphery contract. The invariant that must hold: the address checked against the allowlist must be the address that economically controls the swap input, not the intermediate dispatcher.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension`; add `userA` to the allowlist; do **not** add the router.
2. `userA` calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`. The router calls `pool.swap(recipient, ...)`. `msg.sender` at the pool is the router. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `false` → revert. `userA` cannot use the standard periphery path despite being allowlisted. (**Mode A**)
3. Admin adds the router to the allowlist to fix Mode A: `setAllowedToSwap(pool, router, true)`.
4. `userB` (not on the allowlist, explicitly blocked) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`. The router calls `pool.swap(...)`. `msg.sender` at the pool is the router. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds. `userB` receives pool output tokens despite being a blocked address. (**Mode B**)

Foundry test: deploy pool + extension, configure allowlist with only `userA`, assert `userA` direct swap succeeds and router swap reverts (Mode A); then add router to allowlist, assert `userB` router swap succeeds (Mode B bypass confirmed).
Audit Report

## Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter`: Any User Can Swap on a Restricted Pool When the Router Is Allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is set to `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is used, the router's address becomes `sender`, not the end user's address. If the pool admin allowlists the router to permit approved users to trade through the standard periphery, every unprivileged user can bypass the allowlist by calling the router directly, defeating the entire access-control invariant.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the check at line 37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the contract calling the extension) and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender` to `_beforeSwap` (lines 230–231):

```solidity
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly (lines 72–80):

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The `msg.sender` of `pool.swap()` is the **router**, so the extension receives `sender = router`. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The admin faces an irreconcilable conflict: not allowlisting the router blocks approved users from using the standard periphery; allowlisting the router opens the pool to every unprivileged user. No configuration simultaneously permits approved users through the router while blocking unapproved ones.

## Impact Explanation
This is an admin-boundary break. The `SwapAllowlistExtension` is the production mechanism for restricting swap access to a curated set of counterparties (compliance, institutional-only, toxic-flow prevention). Once the router is allowlisted — the only way to let approved users trade through the standard periphery — the allowlist is completely ineffective for all router-mediated swaps. Any unprivileged address can call `exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) on the router pointing at the restricted pool and execute swaps that the pool admin explicitly intended to block.

## Likelihood Explanation
Likelihood is high. The router is the standard, documented periphery entry point. Pool admins deploying a restricted pool will naturally allowlist the router to serve their approved users. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle` on the router with the restricted pool address.

## Recommendation
The extension must resolve the actual end-user identity rather than the immediate caller of `pool.swap()`. The most robust fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it against the allowlist when `sender` is a known trusted router address. Alternatively, the extension can maintain a registry of trusted router addresses and, when `sender` matches a trusted router, read the actual user from a transient-storage slot written by the router before calling the pool.

## Proof of Concept
```
Setup:
  - Pool P deployed with SwapAllowlistExtension E
  - Admin allowlists Alice: allowedSwapper[P][Alice] = true
  - Admin allowlists Router R: allowedSwapper[P][R] = true
    (necessary so Alice can trade through the router)

Attack (Bob, not allowlisted):
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient, ...) — msg.sender = Router R
  3. Pool calls E.beforeSwap(sender=R, ...)
  4. Extension checks allowedSwapper[P][R] → true ✓
  5. Swap executes for Bob despite Bob not being on the allowlist

Result: Bob swaps on a pool configured to block him.
```
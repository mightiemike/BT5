Audit Report

## Title
SwapAllowlistExtension gates by router address instead of original user, enabling full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is set to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the originating EOA. The extension therefore checks whether the **router** is allowlisted. If the pool admin allowlists the router (the only way to permit router-mediated swaps for legitimate users), every unprivileged address can bypass the allowlist by routing through the public router, completely defeating the extension's purpose.

## Finding Description

**Root cause — pool passes `msg.sender` (the router) as `sender` to the extension:**

In `MetricOmmPool.sol` lines 230–231, `_beforeSwap` is called with `msg.sender` as the first argument:

```solidity
_beforeSwap(
  msg.sender,   // always the router when called via MetricOmmSimpleRouter
  recipient, ...
);
```

**Extension checks `allowedSwapper[pool][sender]` where `sender` is the router:**

`SwapAllowlistExtension.beforeSwap` (lines 37–39) resolves to `allowedSwapper[pool][router]`:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The check never sees the originating EOA.

**Router always calls `pool.swap()` as itself:**

In `MetricOmmSimpleRouter.sol` lines 72–80, `exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original caller's identity:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The same pattern holds for `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165).

**Design inconsistency with `DepositAllowlistExtension`:**

`DepositAllowlistExtension.beforeAddLiquidity` correctly gates by `owner` (the second parameter — the position owner), not by `sender` (the immediate caller). The swap allowlist lacks an equivalent "original actor" parameter in its hook signature, so it can only check the immediate `msg.sender` of `pool.swap()`.

**Existing guards are insufficient:**

There is no mechanism in the router to encode the original user into `extensionData` and no decoding logic in the extension to recover it. The extension has no router registry or fallback identity check.

## Impact Explanation

A pool admin deploys `SwapAllowlistExtension` to restrict trading to KYC-verified counterparties. To allow those users to trade via the standard router, the admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **any unprivileged address** can call `MetricOmmSimpleRouter.exactInputSingle` and the extension passes because it sees `sender = router` which is allowlisted. The allowlist is completely ineffective for router-mediated swaps. Unauthorized traders execute swaps on a restricted pool, draining LP value at oracle prices the pool was not intended to serve to those counterparties. This constitutes a direct loss of LP principal and a broken core pool functionality (the allowlist restriction), meeting Sherlock High/Critical thresholds.

## Likelihood Explanation

The trigger requires no special privilege. Any user who knows the pool uses a `SwapAllowlistExtension` and that the router is allowlisted can call `MetricOmmSimpleRouter.exactInputSingle` directly. The router is a public, permissionless contract. The admin's only alternative — not allowlisting the router — breaks the intended UX for all legitimate users, so in practice the router will be allowlisted on any pool that expects router traffic. The bypass is repeatable and requires no setup beyond knowing the pool address.

## Recommendation

The extension must recover the **original user** rather than the immediate caller. Two options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a coordinated change to the router and extension.

2. **Check `sender` against a router registry and fall through to a user-level check**: If `sender` is a known router, decode the actual user from `extensionData` or transient storage and check that address against the allowlist.

The `DepositAllowlistExtension` correctly gates by `owner` (the position owner passed explicitly as the second parameter), not by `sender` (the liquidity adder contract). The swap allowlist should adopt the same pattern — gate by the economically relevant actor, not the immediate `msg.sender`.

## Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension.
  2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  3. Admin calls setAllowedToSwap(pool, router, true)  // needed for alice to use the router

Attack:
  4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: restrictedPool,
           ...
       })
     The router calls pool.swap(recipient, ...).
     pool passes msg.sender = router to _beforeSwap.
     Extension checks allowedSwapper[pool][router] → true.
     Bob's swap executes on the restricted pool.

Result:
  Bob bypasses the allowlist entirely. The extension never sees Bob's address.

Foundry test outline:
  - Deploy SwapAllowlistExtension, pool, and MetricOmmSimpleRouter.
  - vm.prank(admin); extension.setAllowedToSwap(pool, alice, true);
  - vm.prank(admin); extension.setAllowedToSwap(pool, router, true);
  - vm.prank(bob); router.exactInputSingle({pool: pool, ...});
  - Assert swap succeeds (no NotAllowedToSwap revert) despite bob not being allowlisted.
```
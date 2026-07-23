Audit Report

## Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter: Router Address Checked Instead of Actual Swapper — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's immediate `msg.sender`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router contract, not the originating user. A pool admin who allowlists the router to support router-mediated swaps for allowlisted users inadvertently grants swap access to all users, breaking the per-user access-control invariant.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs the check at line 37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool — which is the pool's own `msg.sender`.

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` at lines 230–240:

```solidity
_beforeSwap(
    msg.sender,   // pool's immediate caller
    recipient,
    ...
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly at lines 72–80, making the pool's `msg.sender` the router contract, not the originating user. The same applies to `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165).

The exploit path is:
1. Pool admin calls `setAllowedToSwap(pool, alice, true)` — allowlists Alice (KYC'd)
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlists router for UX
3. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: curatedPool, ...})`
4. Router calls `pool.swap(...)` → pool's `msg.sender` = router
5. `_beforeSwap(router, ...)` → `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router]` = true → no revert
6. Bob's swap executes despite not being on the allowlist

No existing guard prevents this: the extension has no mechanism to distinguish the router acting on behalf of an allowlisted user from the router acting on behalf of an arbitrary user. The `extensionData` field is available but the current `SwapAllowlistExtension` does not decode it.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position beneficiary passed explicitly by the caller at line 38), not `sender` (the pool's `msg.sender`).

## Impact Explanation

A pool admin who configures a curated pool with `SwapAllowlistExtension` (e.g., for KYC compliance, institutional-only access, or regulatory restrictions) and also allowlists the router to support router-mediated swaps for allowlisted users inadvertently opens the pool to all users. Any non-allowlisted user bypasses the allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool. This is an admin-boundary break reachable by an unprivileged path: the pool admin's explicit per-user access-control configuration is nullified, and LP funds in a curated pool are exposed to trades from actors the pool was explicitly designed to exclude.

## Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router address. This is a reasonable and expected operational action for any curated pool that also wants to support router-mediated swaps for its allowlisted users — the two goals are in direct conflict due to this design flaw. Once the router is allowlisted, any unprivileged user can exploit the bypass with a standard router call requiring no special permissions, no flash loans, and no multi-step setup. The condition is repeatable and permanent until the router is de-allowlisted (which would break router access for legitimate allowlisted users).

## Recommendation

`SwapAllowlistExtension` must check the actual originating user, not the pool's immediate `msg.sender`. Two viable approaches:

1. **Extension-data forwarding**: The router encodes the actual user's address in `extensionData`; the extension decodes and checks that address. The pool admin must trust the router to forward honestly, so this requires the router to be a verified, non-upgradeable contract.
2. **Pool-level effective-swapper field**: The pool passes a separate "effective swapper" argument (e.g., derived from a router-signed payload) to the extension, distinct from the immediate `msg.sender`.

## Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension as before-swap hook.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)   // allowlist Alice (KYC'd)
  3. Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist router for UX

Attack (Bob, not allowlisted):
  4. Bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curatedPool,
         recipient: bob,
         zeroForOne: true,
         amountIn: 1000,
         ...
     });

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=bob, ...)   // pool.msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes for Bob

Result: Bob's swap succeeds despite not being on the allowlist.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, configure as above, assert that `router.exactInputSingle` called by an address not in `allowedSwapper` succeeds when the router is allowlisted.
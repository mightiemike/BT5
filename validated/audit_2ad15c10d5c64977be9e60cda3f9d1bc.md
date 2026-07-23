Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` inside `MetricOmmPool.swap`. When a swap is routed through `MetricOmmSimpleRouter`, that `msg.sender` is the router's address, not the originating user. Any pool admin who allowlists the router to enable legitimate router-mediated swaps simultaneously opens the allowlist to every unprivileged address that calls the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← this is the router when called via MetricOmmSimpleRouter
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` encodes that value and forwards it to every configured extension as `sender`. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is whatever the pool forwarded — the router address. The actual initiating user is stored only in transient storage inside the router for the payment callback (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`) and is never surfaced to the pool or its extensions:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

This creates an inescapable binary: if the router is not allowlisted, no allowlisted user can use the router; if the router is allowlisted, every user — allowlisted or not — can bypass the guard. The same path applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap` with `msg.sender = router`.

## Impact Explanation
The swap allowlist is the primary access-control mechanism for restricted pools (KYC pools, private LP pools, partner-only markets). Once the router is allowlisted — which is required for any allowlisted user to use the standard periphery — the guard is completely neutralised for all router-mediated swaps. Any unprivileged address can execute swaps in a pool explicitly configured to block them, enabling arbitrage or front-running in a pool whose LPs accepted risk only under the assumption that the allowlist was enforced. This constitutes a direct loss of LP value and broken core pool access-control functionality, meeting the Critical/High threshold.

## Likelihood Explanation
High. The router is a public, permissionless contract. No special role, token, or setup is required beyond calling `exactInputSingle`. The pool admin is forced to allowlist the router to make the pool usable for legitimate users, at which point the bypass is unconditionally open to everyone. The condition is self-fulfilling: any real-world deployment of a swap-allowlisted pool that supports router usage is immediately vulnerable.

## Recommendation
The actual initiating user must be threaded through the call chain so the extension can check it. Two viable approaches:

1. **Explicit `originator` parameter on `swap`**: Add an `address originator` field to the pool's `swap` signature (or to `extensionData`) that the router populates with `msg.sender` before calling the pool. The extension reads this field instead of (or in addition to) `sender`.
2. **Extension-data convention**: Define a standard ABI prefix in `extensionData` that the router always prepends with the real user address; `SwapAllowlistExtension` decodes and verifies it, and also verifies that `sender` (the router) is a registered trusted forwarder.

## Proof of Concept
```
Setup
─────
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // Alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required so Alice can use the router

Attack
──────
4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:    <restricted pool>,
           ...
       })

5. Router calls pool.swap(recipient, ...) — msg.sender inside pool = router.

6. Pool calls _beforeSwap(router, recipient, ...).

7. Extension evaluates:
       allowedSwapper[pool][router]  →  true   ✓ (admin set this in step 3)
   → no revert

8. Bob's swap executes successfully despite never being allowlisted.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only `alice` and the router, call `exactInputSingle` from an address that is not `alice`, assert no revert and that output tokens are received.
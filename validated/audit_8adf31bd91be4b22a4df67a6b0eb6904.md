Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper Instead of Actual End User, Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is always `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, the router is the pool's `msg.sender`, so the extension checks whether the **router** is allowlisted rather than the actual end user. If the router is allowlisted to enable legitimate use, every non-allowlisted user can bypass the restriction by calling the router. If the router is not allowlisted, every allowlisted user is locked out of the standard periphery.

## Finding Description
**Root cause:** `MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← router address when called via MetricOmmSimpleRouter
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L162-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, ...)   // sender = router
)
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router — never the actual end user:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly, making the router the pool's `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

The same pattern applies to `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165). No existing guard in the extension, pool, or router recovers the original `tx.origin` or passes the real caller through any trusted channel.

## Impact Explanation
A pool admin deploys a pool with `SwapAllowlistExtension` to restrict trading to KYC'd or institutional counterparties. The allowlist is the sole on-chain mechanism preventing unauthorized users from trading against LP funds.

- **Bypass scenario:** Admin allowlists the router so legitimate users can use the standard periphery. Because the extension checks the router address, every user who calls the router — allowlisted or not — passes the check. Non-allowlisted users can drain LP funds through adverse-selection trades the admin explicitly intended to block. The allowlist invariant is completely broken.
- **Lockout scenario:** Admin does not allowlist the router. Every allowlisted user is blocked from using the standard periphery and must implement the swap callback interface to call the pool directly — effectively breaking pool usability.

This constitutes a broken core pool functionality causing potential loss of funds and an admin-boundary break where an unprivileged path (the standard router) bypasses a pool admin's access control.

## Likelihood Explanation
- `MetricOmmSimpleRouter` is the standard, documented periphery for all swaps. Any user who wants to bypass the allowlist simply calls the router — no special privileges, no malicious setup, no non-standard tokens required.
- The pool admin has no on-chain mechanism to distinguish router-mediated calls from direct calls at the extension level.
- The vulnerability is reachable on every pool that uses `SwapAllowlistExtension` with the router as the intended entry point, which is the expected deployment pattern.

## Recommendation
The extension must gate on the actual end user, not the intermediary. Two approaches:

1. **Pass the original caller through the router.** The router already stores the real `msg.sender` in transient storage as the payer (`_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, msg.sender, ...)`). The pool could accept an explicit `originSender` parameter, or the extension could read it from a trusted router context via `extensionData`.

2. **Attest the real user via `extensionData`.** Require the router to encode the real caller in `extensionData` and have the extension verify the attested address against the allowlist, with the pool/extension trusting only known router addresses as attestors.

As a safe interim measure: document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and revert in `beforeSwap` if `sender` is a known router address, forcing direct pool interaction only.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true).
  - Pool admin calls setAllowedToSwap(pool, router, true)
    so that alice can use the standard periphery.

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...}).
  - Router calls pool.swap(...) with msg.sender = router.
  - Pool calls _beforeSwap(sender = router, ...).
  - Extension checks allowedSwapper[pool][router] → true → swap proceeds.
  - Bob successfully trades on the restricted pool.

Result:
  - Bob bypasses the allowlist entirely.
  - Every non-allowlisted user can do the same via the router.
  - The pool admin's curation policy is completely ineffective.
```
The code confirms the claim. The call chain is:

1. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` — the router is `msg.sender` from the pool's perspective.
2. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 230 — passing the router as `sender`.
3. `ExtensionCalling._beforeSwap` forwards that `sender` (router address) to the extension.
4. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The bypass is real and requires no special setup beyond the router being allowlisted.

---

Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of actual user, enabling allowlist bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` — the router contract — when a user routes through `MetricOmmSimpleRouter`. A pool admin who allowlists the router to support standard periphery usage inadvertently grants every user access, completely defeating the allowlist. Any non-allowlisted user can call `router.exactInputSingle` (or any other router entry point) and execute swaps on a pool designed to be restricted.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap(), i.e. the router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool (correct) and `sender` is the router (wrong — should be the end user). When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap`, the pool sees `msg.sender = router` and the extension checks `allowedSwapper[pool][router]`. If the router is allowlisted (required for any router-mediated swap to work), every user passes the check regardless of whether they are individually allowlisted. The same flaw applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops via `_exactOutputIterateCallback`).

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict access to specific users must allowlist the router for normal periphery usage. Once the router is allowlisted, the allowlist is completely bypassed: any user — including those explicitly excluded — can call any router entry point and execute swaps. Unauthorized users can extract value from a pool designed to be restricted, causing direct loss of LP assets and breaking the core curation invariant. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation
The router is the standard, documented periphery entry point. Any pool admin who wants users to interact normally through the router must allowlist it. The bypass requires no special privileges, no malicious setup, no non-standard tokens, and no privileged role — any user who knows the pool address and the router address can exploit it immediately and repeatedly.

## Recommendation
The pool must pass the original caller's address to the extension, not the intermediary's. Two complementary fixes:

1. **In `MetricOmmPool.swap`:** accept an explicit `sender` parameter (separate from `msg.sender`) that the router can set to the real user address, analogous to Uniswap v4's unlock/action path. The pool should pass this explicit `sender` to `_beforeSwap` instead of `msg.sender`.
2. **In `MetricOmmSimpleRouter`:** if the pool interface is extended, pass `msg.sender` (the real user) as the explicit sender argument when calling `pool.swap`.

A fragile alternative — requiring the router to encode the real user in `extensionData` and decode it in the extension — should be a last resort, as it is not enforced by the pool and can be omitted or spoofed.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin: allowedSwapper[pool][alice] = true    // alice is the intended user
  - Pool admin: allowedSwapper[pool][router] = true   // required for router-mediated swaps

Attack:
  - mallory (not allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: mallory, ...})
  - Router calls pool.swap(...) → pool sees msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true → PASSES
  - Mallory's swap executes on a pool she was not authorized to access

Result:
  - Allowlist is bypassed; mallory extracts value from a curated LP pool
```
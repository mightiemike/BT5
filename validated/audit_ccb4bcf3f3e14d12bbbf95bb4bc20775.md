Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. If the router is allowlisted, every user — including those not individually allowlisted — can bypass the per-user swap gate by routing through the public router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the contract calling the extension), and `sender` is the value the pool passes from its own `msg.sender`.

In `MetricOmmPool.swap`, the pool passes `msg.sender` directly as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` then forwards this value unchanged to the extension via `abi.encodeCall`. When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`), the router calls `pool.swap(...)` directly, making the router the `msg.sender` to the pool. The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

This creates a binary broken invariant: if the router is not allowlisted, all router users are blocked even if individually allowlisted; if the router is allowlisted (the natural operational step to support router-based swaps), all users bypass the per-user allowlist entirely. There is no mechanism in the extension to distinguish individual users routing through the same intermediary.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified counterparties, protocol-internal addresses, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker receives output tokens from the pool without being on the allowlist. This is a direct bypass of a configured access-control guard: the pool transacts with an unauthorized counterparty, violating the invariant that `allowedSwapper[pool][user] == true` is required for a swap to execute. This constitutes broken core pool functionality with direct fund-impacting consequences, as the pool settles swaps with unauthorized counterparties.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public periphery contract callable by any user without restriction. The bypass is triggered whenever: (1) a pool has `SwapAllowlistExtension` configured with `allowAllSwappers[pool] == false`, and (2) the router address is allowlisted for that pool — a natural operational step if the pool admin wants to support router-based swaps for allowlisted users. No privileged access, special tokens, or malicious setup is required; only a standard router call suffices.

## Recommendation
The extension must gate the end-user identity, not the intermediary. The cleanest fix is to require the router to encode `msg.sender` (the originating user) into `extensionData`, and have the extension decode and verify that address against `allowedSwapper`. Specifically:
- `MetricOmmSimpleRouter` should ABI-encode `msg.sender` into the `extensionData` bytes it passes to `pool.swap(...)`.
- `SwapAllowlistExtension.beforeSwap` should decode the originating user from `extensionData` (when present) and check `allowedSwapper[pool][originatingUser]` instead of `allowedSwapper[pool][sender]`.
- A protocol-level convention for `extensionData` encoding should be established and enforced.

Alternatively, document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory or pool configuration level.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)    // allowlist the router
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)
  - Pool admin sets allowAllSwappers[pool] = false

Attack:
  - attacker (not individually allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({pool: pool, amountIn: X, ...})
  - Router calls pool.swap(recipient=attacker, ...)
      → pool's msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - ExtensionCalling forwards sender=router to extension
  - Extension checks: allowedSwapper[pool][router] == true  ✓
  - Swap executes; attacker receives output tokens
  - Per-user allowlist is completely bypassed
```

Foundry test plan: deploy `SwapAllowlistExtension`, configure a pool with it, allowlist only the router, then call `MetricOmmSimpleRouter.exactInputSingle` from an address not in the allowlist and assert the swap succeeds (no `NotAllowedToSwap` revert).
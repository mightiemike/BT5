Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `MetricOmmPool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the router's address. A pool admin who allowlists the router address (the only available mechanism to permit router-mediated swaps) inadvertently grants swap access to every user who calls the router, regardless of individual allowlist status.

## Finding Description

**Root cause in `SwapAllowlistExtension.beforeSwap`** (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`, L37):

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` is the pool; `sender` is the first argument passed by the pool. The extension has no mechanism to decode or recover the originating user's address from `extensionData`.

**How the pool populates `sender`** (`metric-core/contracts/MetricOmmPool.sol`, L230–231):

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap, not the originating user
    ...
```

**How the router calls the pool** (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`, L72–80):

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The router calls `pool.swap` directly. The pool's `msg.sender` is therefore the router contract, not the originating EOA. The same applies to `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165).

**Exploit flow:**

1. Pool admin calls `setAllowedToSwap(pool, router, true)` — the only way to permit any router-mediated swap for allowlisted users, since the extension cannot distinguish originating users.
2. Bob (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
3. Router calls `pool.swap(...)` → pool's `msg.sender = router`.
4. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → passes.
5. Bob's swap executes on the curated pool despite not being individually allowlisted.

**Why existing guards fail:** The extension only has `allowedSwapper[pool][address]` and `allowAllSwappers[pool]`. There is no trusted-router registry, no `extensionData` decoding, and no fallback identity mechanism. The router does not encode the originating user in `extensionData`.

## Impact Explanation

This is a direct policy bypass on curated pools. Pools using `SwapAllowlistExtension` are designed to restrict swap access to specific addresses (e.g., KYC'd market makers, whitelisted counterparties). Once the router is allowlisted — a necessary step for any router-mediated swap — every user can bypass the per-user gate by routing through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps at oracle-derived prices, extracting value from LPs through unfavorable swaps on pools designed to restrict access. This constitutes a broken core pool access control mechanism causing potential loss of LP assets, matching the allowed impact gate for admin-boundary bypass by an unprivileged path.

## Likelihood Explanation

The bypass requires the pool admin to allowlist the router address. This is a natural, non-malicious configuration: any pool admin who wants to support router-mediated swaps for their allowlisted users must allowlist the router, since the extension provides no other mechanism to permit router-originated swaps. The admin cannot allowlist specific users for router paths — the only available granularity is the direct pool caller. The trigger is a reasonable and expected admin action, not a malicious one. Likelihood is **Medium**.

## Recommendation

The extension must gate the economically relevant actor, not the intermediary. Two approaches:

1. **Pass the original user via `extensionData`**: Require the router to encode the originating user (`msg.sender`) in `extensionData` and have the extension decode and check it when `sender` is a registered trusted router.

2. **Trusted-router registry**: Add a registry to the extension. When `sender` is a registered router, decode the real user from `extensionData` and check `allowedSwapper[pool][realUser]` instead.

At minimum, document clearly that allowlisting the router grants access to all router users, and provide a separate per-user router-aware extension variant.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to enable router-mediated swaps for allowlisted users).
  - Alice (allowlisted directly) and Bob (not allowlisted) both exist.

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData).
     → pool's msg.sender = router.
  3. Pool calls _beforeSwap(router, recipient, ...).
  4. ExtensionCalling._beforeSwap passes sender=router to SwapAllowlistExtension.beforeSwap.
  5. Extension checks allowedSwapper[pool][router] → true → passes.
  6. Bob's swap executes on the curated pool despite not being individually allowlisted.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds.

Foundry test outline:
  - deployPool(extensions=[swapAllowlist])
  - swapAllowlist.setAllowedToSwap(pool, router, true)
  - vm.prank(bob); router.exactInputSingle({pool: pool, ...})
  - assert swap succeeds (no revert)
  - assert bob is NOT in allowedSwapper[pool][bob]
```
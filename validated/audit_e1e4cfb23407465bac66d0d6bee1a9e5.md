Audit Report

## Title
`SwapAllowlistExtension` checks router address as `sender` instead of end-user, allowing allowlist bypass or blocking legitimate users — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end-user. This creates an irreconcilable mismatch: allowlisting the router grants every user unrestricted swap access (full allowlist bypass), while allowlisting individual users blocks them from swapping through the router entirely (broken swap flow for legitimate users).

## Finding Description
In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // direct caller of pool.swap() — the router when routing through MetricOmmSimpleRouter
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))` at L162-165. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()`, `sender` is the router address — not the end-user who called the router. The pool admin faces two broken configurations:

- **Path A — allowlist the router:** `allowedSwapper[pool][router] == true` passes for every user who routes through the router. Any unprivileged address executes swaps against a pool configured to deny them.
- **Path B — allowlist individual users:** `allowedSwapper[pool][router] == false` causes revert for every allowlisted user who uses the router, since the router is not in the allowlist.

`DepositAllowlistExtension` does not share this flaw because it checks the explicit `owner` parameter (the position owner), not `sender` (the caller).

## Impact Explanation
A pool deploying `SwapAllowlistExtension` to enforce restricted-access swap markets (institutional-only, KYC-gated, market-maker-only) cannot achieve its intended access control when `MetricOmmSimpleRouter` is in use. Under Path A, the allowlist is a dead letter — any unprivileged address executes swaps against a pool configured to deny them (admin-boundary break, unprivileged path circumvents a configured guard). Under Path B, the allowlist actively breaks the swap flow for every legitimately allowlisted user who uses the router, rendering the pool's primary user-facing entry point unusable (broken core pool functionality). Both outcomes constitute broken core pool functionality and an admin-boundary break.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entry point for the protocol. Any pool that deploys `SwapAllowlistExtension` and expects users to route through the router will encounter this mismatch on every swap attempt. No special attacker capability is required — a standard `exactInputSingle` call suffices. The condition is triggered deterministically on every router-mediated swap to an allowlist-gated pool.

## Recommendation
Pass the true end-user identity through the extension interface. Two options:

1. **Encode the real user in `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Add a `swapper` field to the swap interface:** Introduce an explicit `swapper` parameter to `pool.swap()` (distinct from `sender`/`recipient`) that the pool validates (e.g., `swapper == msg.sender || isApproved`) and forwards to extensions. The extension then checks `allowedSwapper[pool][swapper]` instead of `allowedSwapper[pool][sender]`.

Until fixed, `SwapAllowlistExtension` should document that it only gates direct callers of the pool, not end-users routing through periphery contracts.

## Proof of Concept
```
Path A (bypass):
1. Pool admin deploys pool with SwapAllowlistExtension as beforeSwap guard.
2. Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
   — intending to allow router-based swaps while blocking unknown addresses.
3. Attacker (not individually allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: attacker, ...})
4. Router calls pool.swap(attacker, ...) with msg.sender = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. Extension evaluates: allowedSwapper[pool][router] == true → passes.
7. Swap executes. Attacker receives output tokens. Per-user allowlist fully bypassed.

Path B (DoS for legitimate users):
2b. Pool admin calls swapExtension.setAllowedToSwap(pool, alice, true).
3b. Alice calls router.exactInputSingle({pool: pool, ...}).
4b. Router calls pool.swap(alice_recipient, ...) with msg.sender = router.
5b. Extension evaluates: allowedSwapper[pool][router] == false → reverts NotAllowedToSwap.
6b. Alice cannot swap through the router despite being individually allowlisted.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, configure Path A, call `router.exactInputSingle` from an address not in the allowlist, assert swap succeeds; configure Path B, call from an allowlisted address via router, assert revert with `NotAllowedToSwap`.
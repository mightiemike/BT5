Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the end user. Any pool admin who allowlists the router (required for allowlisted users to use the router) simultaneously opens the gate to every user on the network, completely defeating the per-user access control the extension is designed to enforce.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension via `abi.encodeCall`. `SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool address and `sender` is the pool's `msg.sender` — the router when called via `MetricOmmSimpleRouter.exactInputSingle`. The router calls `pool.swap()` directly with no mechanism to forward the original caller's identity:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
```

The forced dilemma: if the pool admin does **not** allowlist the router, allowlisted users cannot use the router at all. If the admin **does** allowlist the router (the only way to enable router access for allowlisted users), every user on the network can call `router.exactInputSingle()` and the check passes because `allowedSwapper[pool][router] == true`. No existing guard in the router or extension re-checks the original caller's identity.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The unauthorized user receives pool output tokens and LP balances are reduced, constituting a direct loss of LP assets and a broken core pool invariant (curated access). This matches the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds" impact categories.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless periphery contract deployed alongside the protocol. Any user who observes that a pool has a swap allowlist can trivially route through the router instead of calling the pool directly. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices. Likelihood is high whenever a pool admin deploys `SwapAllowlistExtension` and also needs allowlisted users to access the router.

## Recommendation
The extension must verify the original end user, not the immediate caller of `pool.swap()`. Preferred fix: add an `originalSender` field to the hook interface — the pool stores the original `msg.sender` in transient storage at entry and passes it as a separate argument to extensions, distinct from the immediate caller. Alternatively, document that pools using `SwapAllowlistExtension` must not allowlist the router and must require direct `pool.swap()` calls from allowlisted addresses, or have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it (requires a trusted encoding convention).

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][user1] = true   (legitimate allowlisted user)
  allowedSwapper[pool][router] = true  (required for user1 to use the router)

Attack:
  user2 (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: user2, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=user2, ...)   [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ← passes!
      → swap executes, user2 receives tokens

Result:
  user2 swaps successfully despite not being on the allowlist.
  LP assets are transferred to an unauthorized counterparty.
```
Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, allowing full allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender` — the immediate caller of `pool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router (the only way to enable standard periphery UX) inadvertently grants every user on the network the ability to bypass the per-user allowlist by routing through the public router.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap` with its own `msg.sender` as the first argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap; the router when using MetricOmmSimpleRouter
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim as `sender` to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-177
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

**Step 2 — `SwapAllowlistExtension` keys the allowlist check on that `sender`.**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct). `sender` is whoever called `pool.swap` — the router, not the originating user.

**Step 3 — `MetricOmmSimpleRouter` calls `pool.swap` directly, making itself the `sender`.**

For `exactInputSingle` (L71-80) and `exactInput` (L103-112), the router calls `pool.swap(...)` directly. The originating user's address is stored only in transient callback context for payment purposes and is never forwarded to the pool as `sender`.

**Step 4 — The bypass.**

A pool admin who wants to support router-based swaps must call:
```
extension.setAllowedToSwap(pool, address(router), true);
```

Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every call arriving through the router, regardless of who the originating user is. Any non-allowlisted address can call `router.exactInputSingle(pool, ...)` and the guard passes because the check evaluates the router's address, not the user's.

**Existing guards are insufficient:** There is no secondary check on the originating user. The `extensionData` field is passed through but `SwapAllowlistExtension` ignores it entirely. The `recipient` field (second argument, ignored with `address`) is also not checked.

## Impact Explanation

The `SwapAllowlistExtension` is the production mechanism for curated pools restricting trading to specific counterparties (e.g., whitelisted market makers, KYC'd participants, or protocol-controlled addresses). Bypassing it lets any public user execute swaps against a pool designed to be closed. LP assets are exposed to unrestricted arbitrage and directional flow from actors the admin explicitly intended to exclude — constituting direct loss of LP principal and fee revenue on every bypass swap. This is a broken core pool functionality causing loss of funds, matching the allowed impact gate.

## Likelihood Explanation

The bypass requires the pool admin to allowlist the router. This is a natural and expected operational step: any pool that wants to support the standard periphery UX must allowlist the router. The admin has no mechanism to allowlist the router for specific users only — the router is a single address. The misconfiguration is therefore not an edge case; it is the only way to enable router-based swaps on an allowlisted pool. Any unprivileged user can exploit this by simply routing through `MetricOmmSimpleRouter` instead of calling `pool.swap` directly.

## Recommendation

The extension must verify the originating user, not the immediate caller. Two approaches:

1. **Pass the originating user through the router.** The router should forward `msg.sender` as an explicit `originSender` field in `extensionData`, and `SwapAllowlistExtension.beforeSwap` should decode and check that field when `sender` is a known router.

2. **Maintain a trusted-router registry in the extension.** When `sender` is a trusted router, decode the originating user from `extensionData` and check that address against `allowedSwapper` instead of the router address.

The invariant must be: the address checked against the allowlist is the address that economically initiates the swap (the originating user), not the address that mechanically called the pool.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  alice allowlisted: allowedSwapper[pool][alice] = true
  router allowlisted: allowedSwapper[pool][router] = true
    (admin adds router to support standard UX)

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls (MetricOmmSimpleRouter.sol L72-80):
    pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
    // msg.sender of pool = router

  pool calls (MetricOmmPool.sol L230-240):
    _beforeSwap(router, bob, ...)

  ExtensionCalling forwards (ExtensionCalling.sol L160-177):
    extension.beforeSwap(router, bob, ...)

  extension checks (SwapAllowlistExtension.sol L37):
    allowedSwapper[pool][router] == true  ✓  → swap proceeds

Result:
  bob executes a swap on a pool he was explicitly excluded from.
  The allowlist is fully bypassed for any user routing through the router.
```
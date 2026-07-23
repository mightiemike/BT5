Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Originating User, Allowing Any Caller to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is the pool's `msg.sender` — the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the router is added to the allowlist (the necessary step to enable router-based swaps on an allowlisted pool), every user — including those not on the allowlist — can bypass the guard by routing through the public router.

## Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-231
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is used, it calls `pool.swap()` directly at line 72-80, making the router the pool's `msg.sender`. The same holds for `exactInput` (line 104), `exactOutputSingle` (line 136), `exactOutput` (line 165), and the recursive callback path at line 220-228.

In every case, the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`. The originating user's address is never checked.

**Bypass path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses.
2. To allow those addresses to use the standard router UI, the admin must call `setAllowedToSwap(pool, router, true)`.
3. Once the router is allowlisted, any unprivileged user calls `router.exactInputSingle(...)`.
4. The extension sees `sender = router`, which is allowlisted, and the guard passes unconditionally.
5. The original user's address is never checked.

## Impact Explanation

The swap allowlist guard is completely bypassed for any user who routes through the public `MetricOmmSimpleRouter`. Pools intended to be restricted (institutional, KYC-gated, or otherwise access-controlled) become open to arbitrary swappers. Unauthorized actors can execute swaps against the pool's liquidity, draining LP value or manipulating pool state in ways the pool admin explicitly intended to prevent. This constitutes broken core pool functionality with direct LP asset exposure, meeting the "Broken core pool functionality causing loss of funds or unusable swap flows" impact criterion.

## Likelihood Explanation

The router is a public, permissionless contract. Any user who knows its address can call `exactInputSingle` or any other swap function. The only precondition is that the pool admin has added the router to the allowlist — which is the natural and necessary step to enable the standard UI for allowlisted users. The bypass requires no special privileges, no flash loans, and no complex setup. It is trivially repeatable by any address.

## Recommendation

The `SwapAllowlistExtension` should gate the originating user, not the intermediary. Options:

1. **Preferred**: Introduce a dedicated allowlist-aware router that encodes the original caller identity in `extensionData` in a verifiable way (e.g., signed permit or trusted forwarder pattern), and have the extension decode and check that identity.
2. **Document incompatibility**: Require allowlisted users to call the pool directly and document that `MetricOmmSimpleRouter` is incompatible with `SwapAllowlistExtension`.
3. **`tx.origin` fallback**: Acceptable in restricted-pool contexts where the allowlist is the primary control, though not recommended for general use.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  - setAllowedToSwap(pool, router, true)  // router added so alice can use UI

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...)  [MetricOmmSimpleRouter.sol:72-80]
  - Pool calls _beforeSwap(msg.sender=router, ...)  [MetricOmmPool.sol:230-231]
  - Extension checks allowedSwapper[pool][router] → true → passes  [SwapAllowlistExtension.sol:37]
  - Bob's swap executes against the pool's liquidity
  - Allowlist is bypassed; bob is never checked
```
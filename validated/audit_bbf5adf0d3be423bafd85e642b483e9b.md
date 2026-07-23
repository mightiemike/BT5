Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. Any pool admin who allowlists the router to support router-based swaps for legitimate users simultaneously opens the pool to every unprivileged user, completely defeating the allowlist.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the check at line 37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension caller) and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap` (lines 230–231), the pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← this is the router when called via router
    ...
);
```

In `MetricOmmSimpleRouter.exactInputSingle` (lines 72–80), the router calls `pool.swap(...)` directly without forwarding the originating user:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

This means `msg.sender` inside `pool.swap` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, never `allowedSwapper[pool][endUser]`. The same applies to `exactInput` (line 104), `exactOutputSingle` (line 136), `exactOutput` (line 165), and the recursive `_exactOutputIterateCallback` path (line 220), all of which call `pool.swap()` from within the router.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same. If the router is not allowlisted, even legitimate users cannot swap via the router. If the router is allowlisted, every user can bypass the allowlist.

## Impact Explanation
A curated pool (e.g., KYC-only, institutional-only, regulatory-restricted) that configures `SwapAllowlistExtension` and allowlists the router to support normal user flows loses all swap access control for router-mediated paths. Any unprivileged user can swap on the pool by calling the public router. LP providers in the pool are exposed to trades from counterparties the pool admin explicitly intended to exclude. This is an admin-boundary break: an unprivileged path bypasses a configured guard with direct consequences on who can interact with the pool's liquidity.

## Likelihood Explanation
High. The bypass requires no special privileges, no flash loans, and no complex setup. Any user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the allowlisted pool. The router is a public, permissionless contract. The bypass is reachable on every swap through the router on any pool that has allowlisted the router address.

## Recommendation
The extension must check the actual end user, not the immediate pool caller. Two viable approaches:

1. **Forward the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool, and the extension decodes and verifies it. This requires the extension to trust the router, which must be enforced separately (e.g., only accept the forwarded identity when `sender` is a known router).

2. **Check `sender` against a router-aware allowlist**: Maintain a separate mapping of trusted routers. When `sender` is a trusted router, decode the real user from `extensionData`; otherwise check `sender` directly. This preserves direct-pool-call semantics while correctly gating router-mediated calls.

Until fixed, pool admins should be warned that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to let allowlisted users reach the pool via the router.
3. Non-allowlisted attacker calls:
       router.exactInputSingle({
           pool: curated_pool,
           ...
       })
4. Pool calls _beforeSwap(msg.sender=router, ...).
5. Extension evaluates allowedSwapper[pool][router] → true → no revert.
6. Attacker's swap executes on the curated pool.
   allowedSwapper[pool][attacker] was never set to true.
```
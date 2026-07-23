Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the actual end-user, allowing any unprivileged user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension receives `sender = router_address`. Any pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every unprivileged user the ability to bypass the allowlist, because the check resolves to the router's allowlist entry rather than the actual end-user's.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L162-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly from the router contract:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

When a user calls `router.exactInputSingle(...)`, the pool's `msg.sender` is the router, so `sender` passed to the extension is the router address. The allowlist check becomes `allowedSwapper[pool][router_address]`. If the pool admin has allowlisted the router (a necessary step to enable any router-mediated swap), this check passes for every caller regardless of their identity. The same issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` directly from the router. There is no existing guard that recovers the original `msg.sender` from the router context.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers) loses that guarantee entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's liquidity, receiving tokens from LP positions that were only intended to be accessible to allowlisted parties. LP principal is directly at risk because the pool executes swaps and transfers tokens to arbitrary recipients, violating the core invariant that the allowlist gates the economically relevant actor.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool admin who enables router-mediated swaps by allowlisting the router triggers the bypass. The attacker needs no special privilege — only the ability to call a public router function. The condition is reachable on any production pool that uses `SwapAllowlistExtension` with the router allowlisted, which is a normal operational requirement.

## Recommendation

The extension must resolve the actual end-user identity rather than the direct caller of `pool.swap()`. The cleanest fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it, so the allowlist always gates the real initiating user. Alternatively, document that pools using `SwapAllowlistExtension` must not allowlist the router and instead require allowlisted users to call `pool.swap()` directly, though this is operationally limiting.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** call `setAllowedToSwap(pool, eve, true)` — Eve is not allowlisted.
4. Eve calls `router.exactInputSingle({pool: pool, recipient: eve, ...})`.
5. Router calls `pool.swap(eve, ...)` — pool's `msg.sender` is the router (`MetricOmmPool.sol` L231).
6. Pool calls `extension.beforeSwap(router, eve, ...)` via `ExtensionCalling._beforeSwap` (`ExtensionCalling.sol` L165).
7. Extension checks `allowedSwapper[pool][router]` → `true` → passes (`SwapAllowlistExtension.sol` L37).
8. Eve's swap executes and she receives tokens from LP positions that were supposed to be gated.
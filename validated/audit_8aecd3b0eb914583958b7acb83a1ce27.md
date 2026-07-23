Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End User, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates a swap, `sender` is the router address, not the end user. Once the pool admin allowlists the router — a necessary step for any allowlisted user to swap through it — every caller of the router, including non-allowlisted addresses, passes the gate unconditionally.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs its check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` is the pool (correct); `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` — the direct caller of `pool.swap()` — as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` faithfully forwards this value into the extension call. When `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` calls `pool.swap(...)`, `msg.sender` inside the pool is the **router contract**, so `sender` delivered to the extension is the **router address**. The extension therefore evaluates `allowedSwapper[pool][router]`.

For any allowlisted user to swap through the router, the pool admin must call `setAllowedToSwap(pool, router, true)`. The moment the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every call originating from the router — regardless of who called the router. There is no mechanism in the extension to decode the real end user from `extensionData`, and the router encodes no user identity into `extensionData`.

## Impact Explanation
Any unprivileged address can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter`. A pool configured as a private trading venue (e.g., restricted to institutional counterparties or to prevent retail front-running) loses that restriction entirely once the router is allowlisted. Unauthorized swaps execute at oracle-derived prices, exposing LPs to counterparties they explicitly excluded. This is a direct admin-boundary break with LP-asset impact: the pool admin's security configuration is rendered ineffective by an unprivileged path through a public contract.

## Likelihood Explanation
Medium. The precondition — the pool admin allowlisting the router — is the natural and expected operational action whenever the admin wants allowlisted users to use the standard periphery router. It is not an exotic or adversarial setup. Once the router is allowlisted (which is required for legitimate use), the bypass is immediately available to any address that calls the router, with no further preconditions.

## Recommendation
The extension must verify the end user, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData`; have `SwapAllowlistExtension` decode and check it when `sender` is a known router address.
2. **Registry-based fallback**: Maintain a registry of known router addresses in the extension; when `sender` is a registered router, decode and check the user identity from `extensionData`, falling back to `sender` for direct swaps.

The symmetric issue on the `DepositAllowlistExtension` / `MetricOmmPoolLiquidityAdder` path should also be audited.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only `alice` is permitted to swap directly.
3. Admin calls `setAllowedToSwap(pool, router, true)` — required so `alice` can use `MetricOmmSimpleRouter`.
4. `bob` (never allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(recipient=bob, ...)` — inside the pool, `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)` → extension receives `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. `bob`'s swap executes successfully despite never being allowlisted.

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only `alice` and the router, impersonate `bob`, call `exactInputSingle` through the router, assert no revert and tokens transferred.
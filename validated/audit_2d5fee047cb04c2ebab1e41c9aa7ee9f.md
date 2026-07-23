Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks the router's allowlist status rather than the actual user's. Any pool admin who allowlists the router (required for router-based swaps to function) inadvertently opens the allowlist to every user, regardless of their individual allowlist status.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol line 230-231
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    ...
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol lines 149-176
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol lines 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The pool's `msg.sender` is the router, so `sender` in `beforeSwap` is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. There is no mechanism in the extension or the pool to recover the originating user address from `extensionData` or any other source.

The pool admin faces an impossible choice: not allowlisting the router blocks all router-based swaps on the curated pool; allowlisting the router grants every user — including non-allowlisted ones — the ability to bypass the per-user gate by routing through the router.

## Impact Explanation

This breaks the core curation guarantee of `SwapAllowlistExtension`. Non-allowlisted users (e.g., non-KYC'd addresses, blocked counterparties) can execute swaps on pools explicitly configured to restrict them, constituting a broken core pool functionality and an admin-boundary break. The configured allowlist policy is bypassed through the standard, supported periphery path with no special privileges required.

## Likelihood Explanation

Likelihood is high. `MetricOmmSimpleRouter` is the standard periphery swap path. Any pool admin who wants to support router-based swaps on a curated pool must allowlist the router, which automatically opens the bypass to all users. The exploit requires no special privileges, no unusual token behavior, and no complex setup — any user simply calls the router instead of the pool directly.

## Recommendation

The extension must check the actual economic actor, not the intermediary. Two approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before calling the pool. `SwapAllowlistExtension` decodes and checks this address when `sender` is a known trusted router.

2. **Trusted router registry in the extension**: Maintain a registry of trusted routers; when `sender` is a trusted router, decode the actual user from `extensionData` and check that address instead of the router's address.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin allowlists `alice` as a permitted swapper: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists `MetricOmmSimpleRouter` as a permitted swapper (required for router-based swaps): `setAllowedToSwap(pool, router, true)`.
4. `bob` (not allowlisted) calls `pool.swap(...)` directly → extension checks `allowedSwapper[pool][bob]` → `false` → reverts with `NotAllowedToSwap`. ✓
5. `bob` calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)` → router calls `pool.swap()` with `msg.sender = router` → extension checks `allowedSwapper[pool][router]` → `true` → bob's swap executes. ✗

The allowlist is bypassed at step 5 with no special privileges. A Foundry integration test can reproduce this by deploying the extension, configuring the pool, and asserting that a non-allowlisted address succeeds via the router.
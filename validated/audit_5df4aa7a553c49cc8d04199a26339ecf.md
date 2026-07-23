Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing allowlist bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`, which becomes the router contract address when users swap via `MetricOmmSimpleRouter`. `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[pool][sender]` against this router address rather than the originating user. Any non-allowlisted user can bypass the swap allowlist by routing through the public router.

## Finding Description

**Root cause:** In `MetricOmmPool.sol::swap()` (line 231), `msg.sender` is passed as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // <-- this is the router, not the end user
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter::exactInputSingle()`, the router calls `IMetricOmmPoolActions(params.pool).swap(...)` directly. Inside the pool, `msg.sender` is the router contract, so `sender = address(router)` is forwarded to every extension.

`SwapAllowlistExtension::beforeSwap()` then evaluates:

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

Here `msg.sender` is the pool and `sender` is the router. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Exploit path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured, intending to restrict swaps to a set of KYC'd addresses.
2. Pool admin does NOT add attacker's address to `allowedSwapper`.
3. Attacker calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the restricted pool.
4. Router calls `pool.swap(...)` — pool sees `msg.sender = router`.
5. If the router is allowlisted (or `allowAllSwappers` is true), the check passes and the attacker swaps freely.
6. Even if the router is not allowlisted, the pool admin cannot selectively allowlist individual users through the router — they must allowlist the router wholesale, granting access to everyone.

**Existing guards are insufficient:** The `onlyPool` modifier on the extension only verifies the caller is a registered pool, not the identity of the originating user. There is no mechanism in the pool or router to forward the original `msg.sender` to extensions.

## Impact Explanation
The swap allowlist — a core access-control mechanism — is rendered ineffective for any pool using `SwapAllowlistExtension` when the public `MetricOmmSimpleRouter` is deployed. An unprivileged, non-allowlisted user can execute swaps on a restricted pool by routing through the public router. This constitutes a broken core pool functionality and an admin-boundary break: the pool admin's intent to restrict swap access is bypassed by an unprivileged path. Severity: **High**.

## Likelihood Explanation
The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it. The bypass requires no special privileges, no malicious setup, and is repeatable on every block. The only precondition is that the router is allowlisted (or `allowAllSwappers` is true), which is the expected operational state for a usable pool.

## Recommendation
Pass the originating user through the call chain. Options:
1. Add an `originator` parameter to `IMetricOmmPoolActions.swap()` and have the router pass `msg.sender` explicitly; the pool forwards it as `sender` to extensions.
2. Alternatively, have the router implement a trusted forwarding pattern where the pool reads the true payer from transient storage (already used for callback context) and passes that as `sender` to extensions.
3. At minimum, document that `SwapAllowlistExtension` gates the direct caller of `pool.swap()`, not the end user, and require pools using it to disallow router access entirely.

## Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension; do NOT allowlist attacker.
// 2. Allowlist the router (required for normal operation).
// 3. Attacker calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    extensionData: "",
    deadline: block.timestamp
}));
// Pool sees msg.sender = router (allowlisted) → allowlist check passes → swap executes.
// Attacker, who is NOT in allowedSwapper, successfully swaps on a restricted pool.
```
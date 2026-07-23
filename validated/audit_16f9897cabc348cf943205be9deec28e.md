Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` is the router contract address, not the end user. If the router is allowlisted for a pool, every user — including those not individually allowlisted — can bypass the per-user restriction by routing through the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` is the pool (the extension is called by the pool via `_callExtensionsInOrder`). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which receives `msg.sender` of the pool's own `swap()` call:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,  // ← direct caller of pool.swap(), NOT the end user
    ...
);
```

When `MetricOmmSimpleRouter.exactInputSingle` (or any router entry point) is used, the router calls `pool.swap(...)` directly, making the router the `msg.sender` to the pool:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The actual end user's identity is stored only in transient storage via `_setNextCallbackContext` and is never surfaced to any extension. The extension therefore evaluates `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][actual_user]`.

**Exploit path:**
1. Pool admin deploys pool with `SwapAllowlistExtension`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — alice is the only intended swapper.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so alice can use the standard interface.
4. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, bob, ...)`.
7. Extension evaluates: `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes successfully in the restricted pool.

There is no mechanism to simultaneously restrict swaps to specific users and allow those users to swap via the router, because allowlisting the router collapses the per-user gate into an all-or-nothing gate on the router itself.

## Impact Explanation
A pool admin who deploys a pool with `SwapAllowlistExtension` to restrict swaps to a specific set of users (e.g., KYC-verified counterparties) and also allowlists the router to enable standard router-based access will inadvertently open the pool to all users. Any non-allowlisted address can call `router.exactInputSingle(...)` and execute swaps in the restricted pool. This is an admin-boundary break: an unprivileged path (the router) bypasses a pool-admin-configured guard. The pool admin's intended access-control boundary is silently broken.

## Likelihood Explanation
Medium. The router is the standard user-facing entry point for the protocol. A pool admin who wants to restrict swaps to specific users would naturally also allowlist the router so those users can access the pool through the normal interface. The documentation phrase *"Gates `swap` by swapper address"* strongly implies per-user gating, not per-caller-contract gating, making the misconfiguration easy to fall into without reading the implementation carefully.

## Recommendation
1. **Pass the actual user identity through `extensionData`**: The router can encode `msg.sender` (the actual user) into `extensionData`, and the extension can decode and check it. This requires a coordinated change to the router and extension.
2. **Document the actual behavior clearly**: State explicitly that `sender` is the direct pool caller (the router when using `MetricOmmSimpleRouter`), not the end user, and that allowlisting the router grants access to all router users.
3. **Align with the deposit allowlist pattern**: `DepositAllowlistExtension` correctly checks `owner` (the beneficiary), not `sender` (the operator). For swaps, an analogous "beneficiary" identity should be checked instead of the direct pool caller.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin: swapExtension.setAllowedToSwap(pool, alice, true)
   → alice is the only intended swapper.
3. Pool admin: swapExtension.setAllowedToSwap(pool, router, true)
   → router is allowlisted so alice can use the standard interface.
4. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, ...) with msg.sender = router.
6. Pool calls extension.beforeSwap(router, bob, ...).
7. Extension evaluates: allowedSwapper[pool][router] == true → passes.
8. bob's swap executes successfully in the restricted pool.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only `alice` and the router, call `router.exactInputSingle` from `bob`, assert the swap succeeds without revert.
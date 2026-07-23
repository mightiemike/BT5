Audit Report

## Title
SwapAllowlistExtension Sender Binding Passes Router Address Instead of Actual Trader, Enabling Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
When a swap is routed through `MetricOmmSimpleRouter`, the pool's `swap()` function receives `msg.sender = router` and passes that address as `sender` to `_beforeSwap`. `SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][router]` instead of the actual end-user. If the router is allowlisted (the only way to permit router-mediated swaps for any user), every unprivileged trader can bypass the per-user allowlist gate entirely by routing through the public router.

## Finding Description
**Call path:**

1. User (not on allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — `msg.sender` seen by the pool is the router address.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender /*= router*/, recipient, ...)`.
4. `ExtensionCalling._beforeSwap` ABI-encodes `sender = router` and dispatches to `SwapAllowlistExtension.beforeSwap`.
5. `SwapAllowlistExtension.beforeSwap` evaluates:
   ```solidity
   if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
       revert NotAllowedToSwap();
   }
   ```
   Here `msg.sender` is the pool and `sender` is the router — the actual end-user identity is never consulted.

**Root cause:** `MetricOmmPool.swap` binds `sender` to `msg.sender` (the immediate caller), not to the originating trader. The router is a public, permissionless contract; any EOA can call it. If the pool admin allowlists the router address to enable router-mediated swaps for legitimate users, the check degenerates to "is the router allowlisted?" — which is always true for all users.

**Existing guards insufficient:** The `onlyPool` modifier on the extension only verifies the caller is a registered pool; it does not recover the original user. No mechanism in the pool or router threads the real `msg.sender` of the router call through to the extension.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise vetted addresses is fully bypassed by any unprivileged trader routing through `MetricOmmSimpleRouter`. The allowlist — the sole access-control mechanism for swap gating — provides no protection. This is broken core pool functionality (allowlist gate) causing the pool to accept swaps from actors it was explicitly configured to reject, meeting the "broken core pool functionality" and "admin-boundary break" impact criteria.

## Likelihood Explanation
The `MetricOmmSimpleRouter` is a public, permissionless contract deployed alongside the protocol. Any trader can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` with no preconditions. The bypass requires zero privileged access, zero capital beyond the swap amount, and is repeatable on every block. Any pool that uses `SwapAllowlistExtension` and needs to support router-mediated swaps is permanently vulnerable.

## Recommendation
Pass the originating user identity through the swap path. Two options:

1. **Preferred — thread `recipient` or an explicit `trader` parameter:** Add an optional `trader` field to `swap()` parameters (defaulting to `msg.sender`) that the pool passes as `sender` to extension hooks. The router sets this to `msg.sender` before calling the pool.
2. **Alternative — check `tx.origin` in the extension:** Replace `sender` with `tx.origin` inside `SwapAllowlistExtension.beforeSwap`. This is simpler but incompatible with smart-contract traders and introduces `tx.origin` risks.

The cleanest fix is option 1: the router already stores the real payer in transient storage (`_setNextCallbackContext(..., msg.sender, ...)`); expose that as the `trader` argument forwarded to hooks.

## Proof of Concept
```solidity
// Pool configured with SwapAllowlistExtension; only `alice` is allowlisted.
// Pool admin also allowlists the router so alice can use it.
allowedSwapper[pool][alice] = true;
allowedSwapper[pool][router] = true; // required for router path to work for alice

// Attacker (bob, not allowlisted) calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1e18,
    ...
}));
// Pool receives msg.sender = router → sender = router → allowedSwapper[pool][router] == true → swap succeeds.
// Bob bypasses the allowlist entirely.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only `alice` and the router, then `vm.prank(bob); router.exactInputSingle(...)` and assert it does **not** revert — demonstrating the bypass.
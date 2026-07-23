Audit Report

## Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. If the pool admin allowlists the router to support router-mediated swaps, any user can bypass the allowlist by routing through the router, fully defeating the curated pool's access control.

## Finding Description

In `SwapAllowlistExtension.beforeSwap`, the allowlist check is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool via `ExtensionCalling._callExtensionsInOrder`), and `sender` is the first argument forwarded from the pool. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // becomes `sender` in the extension
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` then encodes this as the first argument to `IMetricOmmExtensions.beforeSwap`. When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The pool's `msg.sender` is the router, so `sender = router` is forwarded to the extension. The extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap(...)` directly from the router.

The `onlyPool` modifier in `BaseMetricExtension` correctly restricts who can call the extension, but it does not fix the wrong-actor binding — the pool is a legitimate caller, but it forwards the router's address as the swapper identity rather than the original user.

**Attack path:**
1. Pool admin deploys a curated pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. To support the official router, the admin allowlists the router: `setAllowedToSwap(pool, router, true)`.
3. Any non-allowlisted user calls `router.exactInputSingle({pool: pool, ...})`.
4. The pool passes `sender = router` to the extension.
5. The extension checks `allowedSwapper[pool][router] == true` → passes.
6. The non-allowlisted user successfully swaps on the curated pool.

## Impact Explanation

Any user can bypass the swap allowlist on a curated pool by routing through `MetricOmmSimpleRouter`. The allowlist — the primary access-control mechanism for curated pools — is rendered entirely ineffective. Non-allowlisted users can execute swaps at oracle prices, draining pool liquidity and directly harming LP principal. This constitutes broken core pool functionality causing direct loss of funds.

## Likelihood Explanation

Medium. Requires a pool with `SwapAllowlistExtension` configured and the router allowlisted. Pool admins who want to support both allowlisted direct swaps and router-mediated swaps will naturally allowlist the router, triggering the bypass. The exploit requires no special privileges or setup beyond using the public router. The `MetricOmmSimpleRouter` is the canonical periphery entry point documented for end users.

## Recommendation

The extension must check the original user identity, not the immediate pool caller. Concrete options:

1. **Router-forwarded identity**: Have the router encode the original `msg.sender` into `extensionData` and have the extension decode and verify it. This requires the extension to trust the router as a forwarder.
2. **Transient initiator slot**: Have the pool record the original transaction initiator in transient storage and expose it to extensions via a dedicated getter, so extensions can check the true originator.
3. **Documentation guard**: If neither fix is applied, document explicitly that `SwapAllowlistExtension` is incompatible with router-mediated swaps and that allowlisting the router opens the pool to all users — and enforce this at the admin setter level by reverting if the router address is supplied.

## Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin calls setAllowedToSwap(pool, router, true)   // to support router swaps
  admin does NOT allowlist attacker

Attack:
  attacker calls router.exactInputSingle({pool: pool, recipient: attacker, ...})
  router calls pool.swap(attacker, zeroForOne, amount, limit, "", extensionData)
  pool calls _beforeSwap(msg.sender=router, ...)
  extension checks allowedSwapper[pool][router] == true  → passes
  swap executes; attacker receives output tokens from curated pool
  allowlist policy is fully bypassed
```
Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the router is allowlisted — the natural configuration for any pool intended to be accessible via the supported periphery — every user in the world can bypass the per-user swap allowlist by routing through the router.

## Finding Description
In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // ← this is the ROUTER when called via MetricOmmSimpleRouter
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension via `abi.encodeCall`:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly — there is no mechanism to forward the original `msg.sender` (alice) to the pool:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

**Bypass path:** A pool admin who wants to allow router-mediated swaps calls `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every call that comes through the router — regardless of who the actual user is. Any unprivileged user can now swap on the curated pool by calling `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router.

**Secondary breakage:** A pool admin who allowlists `alice` directly (`setAllowedToSwap(pool, alice, true)`) will find that alice cannot swap through the router (the check sees `router`, not `alice`), forcing her to call the pool directly — which requires implementing `IMetricOmmSwapCallback`, an unreasonable burden for an EOA.

No existing guard compensates for this: `SwapAllowlistExtension` has no fallback check on `recipient` or any other field, and the pool interface provides no separate "original initiator" parameter.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The allowlist — the sole access-control mechanism on the swap path — silently fails open. Unauthorized users can execute swaps, drain LP-provided liquidity at oracle prices, and extract value from a pool that was designed to be closed to them. This constitutes broken core pool functionality causing direct loss of LP assets, meeting the contest's Critical/High impact threshold.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported swap entrypoint for the protocol. Any pool admin who configures a swap allowlist and also wants their pool to be accessible via the router will naturally allowlist the router — this is the expected and documented configuration. The bypass requires no special privileges, no malicious setup, no non-standard tokens, and no flash loans — only a call to the public `exactInputSingle` or `exactInput` function on the router. The condition is trivially reachable by any unprivileged user.

## Recommendation
The extension must check the economically relevant actor — the original user — not the intermediary router. Options:

1. **Preferred:** Add a dedicated `swapper` (original initiator) field to the extension hook interface that the pool populates before any router indirection. The pool would need to receive this from the router, e.g., via a dedicated parameter or transient storage.
2. **Router-level:** Have the router encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it (requires trust that the pool does not allow spoofed `extensionData`).
3. **Extension-level workaround:** Check `recipient` instead of `sender` if the intended gated identity is the token recipient — but this only works if `recipient` reliably identifies the authorized party, which is not guaranteed.

The cleanest fix is for the pool to expose the original initiator as a distinct parameter in the hook interface, separate from the intermediary `sender`.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — intending to allow router-mediated swaps for authorized users.
3. Pool admin does **not** call `setAllowedToSwap(pool, alice, true)` — alice is not an authorized swapper.
4. Alice calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: alice, ...})`.
5. Router calls `pool.swap(alice, ...)` — pool's `msg.sender` is the router.
6. Pool calls `_beforeSwap(router, alice, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Alice successfully swaps on a pool she was never authorized to access, bypassing the allowlist entirely.
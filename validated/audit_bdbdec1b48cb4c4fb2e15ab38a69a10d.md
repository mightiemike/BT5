Audit Report

## Title
`SwapAllowlistExtension` checks router address as `sender` instead of actual end-user, allowing allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual end-user. Any pool admin who allowlists the router to support standard periphery access inadvertently grants every address on the network the ability to swap on the curated pool.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension via `abi.encodeCall`:

```solidity
// ExtensionCalling.sol L162-176
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router — the lookup is `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` stores the actual user's address only in transient storage via `_setNextCallbackContext` and never surfaces it to the pool or extension:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The extension has no mechanism to recover the true initiator. When the router is allowlisted, `allowedSwapper[pool][router]` resolves to `true` for every caller regardless of their individual allowlist status.

## Impact Explanation
A curated pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of counterparties loses its access-control guarantee the moment the pool admin allowlists the router. Any address on the network can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and trade on the pool as if they were allowlisted. This is a direct admin-boundary break: the pool admin's configured policy is bypassed by an unprivileged path through a supported production contract. The wrong value is the extension decision (`allowedSwapper[pool][router]` evaluated instead of `allowedSwapper[pool][actualUser]`), causing the `beforeSwap` hook to return the success selector for unauthorized traders.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Pool admins who deploy curated pools and want to support standard tooling (wallets, aggregators, front-ends) will allowlist the router. The bypass is immediately reachable by any address with no special privileges, no flash loan, and no multi-transaction setup — a single call to `exactInputSingle` suffices.

## Recommendation
The extension must gate on the economic actor, not the intermediary. The most robust fix is to have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it when the direct `sender` is a known router. Alternatively, add a dedicated `originalCaller` field or transient slot to the pool's `swap` path so the true initiator is always surfaced to extensions without relying on `extensionData` conventions. The `DepositAllowlistExtension` pattern — checking `owner`, which the adder sets to the actual position owner — is the correct model and should be replicated for the swap path.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. `alice` (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool.
4. The router calls `pool.swap(...)` — pool's `msg.sender` is the router.
5. Pool calls `_beforeSwap(router, ...)` → `ExtensionCalling` encodes `sender = router`.
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
7. The swap executes successfully for `alice` despite her not being allowlisted.
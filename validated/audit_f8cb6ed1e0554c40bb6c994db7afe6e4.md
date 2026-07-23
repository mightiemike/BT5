Audit Report

## Title
`SwapAllowlistExtension.beforeSwap()` checks the router address instead of the end-user, allowing any caller to bypass per-user swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` receives `sender` as the `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end-user. A pool admin who allowlists the router so that authorized users can use it inadvertently grants every caller access, completely neutralizing the per-user allowlist.

## Finding Description
`SwapAllowlistExtension.beforeSwap()` performs its identity check against `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool via `_callExtensionsInOrder`). `sender` is the first argument forwarded by `MetricOmmPool.swap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← caller of pool.swap() — the router, not the end-user
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router stores the real payer in transient storage and then calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The real end-user address (`msg.sender` of the router call) is stored only in transient storage for the payment callback and is **never forwarded to the extension**. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

A pool admin who wants allowlisted users to use the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every call arriving through the router, regardless of who the actual caller is. The per-user allowlist is completely neutralized for the router path.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity()` correctly checks `owner` (the position owner, a separate explicit parameter) rather than `sender` (the liquidity adder/caller), which is the correct pattern for identity verification.

## Impact Explanation
Any unprivileged address can swap in a pool the admin intended to restrict to a specific set of addresses by routing through `MetricOmmSimpleRouter`. This matches the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds" impact categories. Depending on the pool's purpose (institutional-only pricing, KYC-gated liquidity, rate-limited market-making), unauthorized users can access favorable oracle-driven pricing not intended for them, draining LP value at rates LPs did not consent to.

## Likelihood Explanation
The bypass requires only that the pool admin allowlists the router — a natural and expected action for any pool that wants its allowlisted users to benefit from the router's slippage protection. The `SwapAllowlistExtension` documentation states it "Gates `swap` by swapper address, per pool" with no warning that allowlisting the router collapses all users into a single identity. No special privileges, flash loans, or oracle manipulation are required; a standard `exactInputSingle` call suffices.

## Recommendation
Pass the actual end-user address through the hook chain. Two options:

1. **Router-side**: Store the real payer in transient storage and expose it via a standard interface (e.g., `IMetricOmmSwapInitiator`) that the extension can call back into the router to retrieve the originating address.
2. **Extension-side**: Change `SwapAllowlistExtension` to check `sender` only when `sender` is not a known router, and require routers to forward the real user address in `extensionData` (with the extension decoding and verifying it). The pool admin would configure trusted router addresses separately.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the position owner) rather than `sender` (the liquidity adder), which is the correct pattern.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps in a pool he was never authorized to access.

Direct pool call by Bob (`pool.swap(...)`) would correctly revert because `allowedSwapper[pool][bob]` is `false`. The bypass is exclusive to the router path.
Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any Caller to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension always evaluates the router's address — not the actual end user. To allow any allowlisted user to swap via the router, the pool admin must add the router to the allowlist. Once the router is allowlisted, every address on the network can bypass the allowlist by calling the router.

## Finding Description
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` at line 230–231, passing its own `msg.sender` (the direct caller) as `sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this is the router when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes that `sender` value and forwards it verbatim to the extension (lines 162–176). `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct key) and `sender` is the router — not the end user. When `MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` (lines 72–80), the pool's `msg.sender` is the router. The same applies to `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165). The extension therefore evaluates `allowedSwapper[pool][router]`, never `allowedSwapper[pool][user]`.

For any allowlisted user to swap via the router, the admin must call `setAllowedToSwap(pool, router, true)`. The moment the router is allowlisted, the guard is fully open: any address can call `exactInputSingle` on the router and the extension passes because it sees the allowlisted router address.

## Impact Explanation
A pool deployer who wants to restrict swaps to a curated set of addresses (e.g., KYC'd wallets) deploys with `SwapAllowlistExtension`. The allowlist provides zero protection against non-allowlisted users on the router path once the router is added. This is a direct, complete bypass of a core access-control mechanism — non-permitted actors can swap on curated pools, constituting broken core pool functionality with fund-impacting consequences.

## Likelihood Explanation
The router is the primary user-facing entry point. Any pool admin who deploys `SwapAllowlistExtension` and wants their allowlisted users to use the router will inevitably add the router to the allowlist, triggering the bypass. The attacker needs no special privilege — a single call to `exactInputSingle` suffices.

## Recommendation
Pass the original end-user address through the swap path so the extension can gate on it. Two options:

1. **Pool-level**: Have the pool accept an explicit `originator` parameter in `swap()` and forward it as `sender` to extensions, with the router populating it as `msg.sender` before calling the pool.
2. **Extension-level**: Require the router to pass the real user address in `extensionData` and decode it in `SwapAllowlistExtension.beforeSwap`, checking that decoded address instead of `sender`.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the LP position beneficiary), which is explicitly supplied by the caller and is independent of the router address.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin allowlists alice (KYC'd user) via setAllowedToSwap(pool, alice, true)
  - Admin allowlists router via setAllowedToSwap(pool, router, true)
    (required so alice can use the router)

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) → pool's msg.sender = router
  - Pool calls _beforeSwap(msg.sender=router, ...) → extension receives sender=router
  - Extension checks allowedSwapper[pool][router] → true
  - Swap executes for bob despite bob not being on the allowlist

Result:
  - SwapAllowlistExtension is fully bypassed for any caller via the router
```
Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate `msg.sender` of `pool.swap`. When `MetricOmmSimpleRouter` calls `pool.swap`, the pool's `msg.sender` is the router contract, so the allowlist checks the router's address rather than the originating user. Any pool that allowlists the router to support router-mediated swaps for legitimate users simultaneously grants unrestricted swap access to every unprivileged user who routes through the router.

## Finding Description
In `MetricOmmPool.swap`, `_beforeSwap` is called with `msg.sender` as the `sender` argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← immediate caller, not original EOA
  recipient, zeroForOne, amountSpecified, priceLimitX64,
  packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
```

`ExtensionCalling._beforeSwap` forwards that `sender` value unmodified to every configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`.

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the address the pool forwarded — the router contract when routing through `MetricOmmSimpleRouter`.

`MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` all call `IMetricOmmPoolActions(pool).swap(...)` directly with no encoding of the original `msg.sender` into `extensionData`. The `bytes calldata` extensionData parameter received by `beforeSwap` is accepted but entirely ignored — there is no decoding path to recover the originating EOA. A pool admin who wants allowlisted users to trade via the router must call `setAllowedToSwap(pool, router, true)`. Once that entry exists, `allowedSwapper[pool][router] == true` passes for every caller of the router, regardless of whether that caller is individually permitted.

## Impact Explanation
The swap allowlist access control is completely nullified for all router-mediated swaps. Any unprivileged user can trade against a restricted pool by routing through `MetricOmmSimpleRouter`, bypassing the per-address gating the pool admin intended to enforce. LPs in restricted pools are exposed to adverse selection, front-running, or targeted value extraction by users the pool admin explicitly excluded. This constitutes a broken core pool functionality causing direct loss of LP assets and constitutes an admin-boundary break where an unprivileged path bypasses a configured access control.

## Likelihood Explanation
The bypass requires the router to be allowlisted, which is a necessary and expected configuration for any pool that supports router-mediated swaps for its allowlisted users. Any pool admin who configures both the allowlist extension and router support will inadvertently expose the bypass. The trigger is any unprivileged user calling the public router functions (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`), which require no special permissions.

## Recommendation
The `SwapAllowlistExtension` must check the original user's address rather than the immediate caller. Two viable approaches:

1. **`extensionData` forwarding**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router. The pool's `_beforeSwap` already forwards `extensionData` unmodified to extensions.
2. **Two-level check**: If `sender` is a registered router address, decode the original user from `extensionData`; otherwise check `sender` directly. This requires a router registry in the extension.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — `alice` is a trusted trader.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary to allow `alice` to use `MetricOmmSimpleRouter`.
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the restricted pool.
5. The router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — `msg.sender` inside the pool is the router address.
6. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender=router, ...)`.
7. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router] == true` → passes.
8. The swap executes. `bob` successfully trades against the restricted pool, bypassing the allowlist entirely.

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist `alice` and the router, then call `exactInputSingle` from an unallowlisted address and assert the swap succeeds rather than reverting with `NotAllowedToSwap`.
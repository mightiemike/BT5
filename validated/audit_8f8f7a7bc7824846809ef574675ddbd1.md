Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass or Complete Lockout via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the direct pool caller. When `MetricOmmSimpleRouter` intermediates, `sender` is the router address, not the end user. This makes the allowlist check resolve to `allowedSwapper[pool][router]`, completely decoupling the gate from the actual trader identity. Any pool deploying both `SwapAllowlistExtension` and `MetricOmmSimpleRouter` is in an irrecoverably broken state: allowlisting the router opens swaps to every user; not allowlisting it blocks every allowlisted user from using the standard entry point.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value as `sender` to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L149-177
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The effective check is `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without encoding the originating user anywhere the extension can read:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(params.recipient, params.zeroForOne, ...);
```

`msg.sender` (the real user) is stored only in transient callback context for payment purposes — it is never passed into `extensionData` or any swap parameter visible to extensions.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position owner, explicitly passed as a separate parameter in `addLiquidity`), which is the actual user regardless of who calls `addLiquidity`. The `swap` interface has no equivalent `owner`/`originator` field, so there is no correct value for `SwapAllowlistExtension` to check when a router is involved.

## Impact Explanation
**Bypass path (router allowlisted):** A pool admin adds the router to the allowlist intending to permit router-mediated swaps for approved users. This immediately grants every user — including those explicitly excluded — the ability to swap through the router. The allowlist is completely defeated. Any user can extract tokens from the pool at oracle prices during a restricted phase (e.g., pre-DEX-listing, post-incident investigation), constituting direct loss of pool principal.

**Lockout path (router not allowlisted):** A pool admin allowlists individual users but not the router. Those users cannot swap through `MetricOmmSimpleRouter` because the extension sees `sender = router` and reverts with `NotAllowedToSwap`. The standard periphery entry point is broken for all allowlisted users, rendering the swap allowlist unusable with the production router — broken core pool swap functionality.

Both outcomes are fund-impacting and meet Sherlock Critical/High thresholds.

## Likelihood Explanation
The `SwapAllowlistExtension` is a production periphery contract intended for real pool deployments. `MetricOmmSimpleRouter` is the standard swap entry point. Any pool that configures both — the expected production setup — will encounter one of the two broken states above. The admin has no configuration path that correctly enforces per-user allowlisting through the router. The trigger is a normal user swap through the router, which is the expected usage pattern. No special attacker capability is required beyond calling `router.exactInputSingle()`.

## Recommendation
The `beforeSwap` hook receives `sender` (direct pool caller) and `recipient`. Neither is the end user when a router is involved. The preferred fix is to add an explicit `originator` address to the `pool.swap()` interface (analogous to `owner` in `addLiquidity`), set by the router to `msg.sender`, and have `SwapAllowlistExtension` check `allowedSwapper[pool][originator]`. Alternatively, require allowlisted users to call `pool.swap()` directly and document that the router is incompatible with `SwapAllowlistExtension`.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `swapExtension.setAllowedToSwap(pool, router, true)` — intending to allow router-mediated swaps for approved users.
3. A non-allowlisted `attacker` calls `router.exactInputSingle(...)`.
4. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `attacker` successfully swaps despite never being added to the allowlist.

Alternatively (lockout path):
1. Admin calls `swapExtension.setAllowedToSwap(pool, user1, true)` but does NOT allowlist the router.
2. `user1` calls `router.exactInputSingle(...)`.
3. Extension evaluates `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap` revert.
4. `user1` cannot swap through the standard router despite being explicitly allowlisted.
Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address as the swapper identity, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the router is `msg.sender`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. If the pool admin adds the router to the allowlist to enable router-based swaps for permitted users, every unpermitted user can bypass the per-user restriction by routing through the router.

## Finding Description
The extension checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` is the pool (correct namespace key). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`.

`MetricOmmPool.swap()` passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    ...
```

`ExtensionCalling._beforeSwap` forwards it unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`MetricOmmSimpleRouter` calls `pool.swap()` directly without passing the originating user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L136-137
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

The same pattern applies to `exactInputSingle` (L72-80), `exactInput` (L104-112), and `exactOutput` (L165-181). In every case, `msg.sender` inside the pool is the router, so `sender` passed to the extension is the router address, not the actual user.

`DepositAllowlistExtension` does not share this bug because `addLiquidity` accepts an explicit `owner` parameter that the extension checks, independent of who called `addLiquidity`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32,38
function beforeAddLiquidity(address, address owner, ...)
    ...
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

`swap()` has no equivalent explicit swapper parameter, so the extension has no way to recover the actual user from the call arguments.

## Impact Explanation
**Scenario A — Allowlist bypass (High):** A pool admin deploys `SwapAllowlistExtension` to restrict swaps to KYC'd users. They add individual users and also add the router so those users can use the standard periphery path. Any unpermitted address can call `router.exactInputSingle(...)` targeting the pool; the extension sees `sender = router`, finds `allowedSwapper[pool][router] = true`, and passes. The per-user restriction is completely defeated — every unpermitted user can trade in a pool that was supposed to be curated. This is a direct admin-boundary break and broken core pool access-control functionality.

**Scenario B — Broken core swap functionality (Medium):** If the admin does not add the router, even allowlisted users cannot use `MetricOmmSimpleRouter` — the extension checks `allowedSwapper[pool][router]` which is `false` and reverts. The only usable path is calling `pool.swap()` directly and implementing `metricOmmSwapCallback`, which is not the supported periphery path.

## Likelihood Explanation
Medium-High. `MetricOmmSimpleRouter` is the documented production periphery swap path. Any pool deploying `SwapAllowlistExtension` that also wants users to swap through the router must add the router to the allowlist — the natural configuration. Once the router is allowlisted, the bypass is available to any address with no special privilege, requiring only a call to the public `exactInputSingle`, `exactOutputSingle`, `exactInput`, or `exactOutput` functions.

## Recommendation
The extension must gate on the actual initiating user, not the intermediary router. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`. The extension decodes and verifies it. This requires a trusted encoding convention between the router and the extension.

2. **Add an explicit `swapper` parameter to `pool.swap()`**: The pool's `swap()` signature accepts a `swapper` address (analogous to how `addLiquidity` accepts `owner`), and the pool passes it to `_beforeSwap` instead of `msg.sender`. The router sets `swapper = msg.sender` (the actual user). This mirrors how `DepositAllowlistExtension` correctly checks `owner` rather than `sender`.

## Proof of Concept
```
1. Deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowAllSwappers(pool, false)  [default — deny all].
3. Pool admin calls setAllowedToSwap(pool, alice, true)   [permit alice].
4. Pool admin calls setAllowedToSwap(pool, router, true)  [permit router so alice can use it].
5. charlie (not on allowlist) calls:
       router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})
6. Router calls pool.swap(recipient=charlie, ...) — msg.sender inside pool = router.
7. Pool calls _beforeSwap(sender=router, recipient=charlie, ...).
8. Extension evaluates: allowedSwapper[pool][router] == true → passes.
9. charlie's swap executes successfully in a pool that should have blocked him.
```

Root cause confirmed at:
- [1](#0-0)  — checks `sender` (the router) instead of the actual initiating user
- [2](#0-1)  — unconditionally forwards `msg.sender` (the router) as `sender` to all swap hooks
- [3](#0-2)  — router calls `pool.swap()` without encoding the originating user

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-38)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

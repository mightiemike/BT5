Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool (the extension is called via `extension.call(data)` from the pool) and `sender` is whoever called `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender` = router address — the actual end-user is never forwarded. This makes it structurally impossible to enforce a per-user allowlist for router-mediated swaps: allowlisting the router grants access to every caller, while not allowlisting it blocks all allowlisted users from using the router.

## Finding Description
**Root cause — `SwapAllowlistExtension.beforeSwap`:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool, confirmed by two facts: (1) `CallExtension.callExtension` uses `extension.call(data)` (a regular external call), so `msg.sender` in the extension is the pool; (2) `BaseMetricExtension.onlyPool` enforces this. `sender` is the first argument forwarded from `MetricOmmPool.swap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    ...
```

**Exploit path — `MetricOmmSimpleRouter.exactInputSingle`:**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // user-controlled bytes, no identity enforcement
    );
```

The router calls `pool.swap()` directly. `msg.sender` to the pool is the router. The pool forwards `msg.sender` (= router) as `sender` to `_beforeSwap`, which passes it to the extension. The actual end-user (`msg.sender` to the router) is never included in any argument the extension receives. `extensionData` is passed through verbatim from the user with no enforced identity encoding.

**Result:** The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Existing guards are insufficient:** `BaseMetricExtension.onlyPool` only verifies the caller is a registered pool — it does not help recover the true initiator. There is no on-chain mechanism in the current call path to propagate the original user address to the extension.

## Impact Explanation
A pool admin deploying `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise vetted addresses loses that protection entirely for router-mediated flows. If the router is allowlisted (the only way to let allowlisted users swap through the standard interface), any unprivileged address can call `router.exactInputSingle` targeting the curated pool and the check passes. LP assets are exposed to swaps from actors the pool admin explicitly intended to exclude, resulting in direct loss of LP principal through adverse selection or regulatory non-compliance. This is an admin-boundary break: an unprivileged caller bypasses a pool-admin-configured access control gate via a public protocol contract.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants allowlisted users to swap through the standard router must allowlist the router address. Once the router is allowlisted, the bypass requires a single `exactInputSingle` call with no special privileges, no flash loan, and no multi-step setup. The condition is trivially reachable by any EOA or contract.

## Recommendation
The extension must gate the actual end-user, not the intermediary. The cleanest fix is to define a standard `extensionData` encoding that includes the originating user address, require the router to populate it (encoding `msg.sender` before calling `pool.swap()`), and have the extension decode and verify it:

```solidity
// In the extension:
address user = abi.decode(extensionData, (address));
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][user]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

```solidity
// In the router (exactInputSingle):
bytes memory effectiveExtensionData = abi.encode(msg.sender);
IMetricOmmPoolActions(params.pool).swap(..., effectiveExtensionData);
```

Pool admins would then allowlist user addresses, not the router. The extension should also validate that `extensionData` is non-empty and correctly sized to prevent a user from passing empty bytes to bypass the decode.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists router: extension.setAllowedToSwap(pool, address(router), true)
  - Pool admin does NOT allowlist attacker: allowedSwapper[pool][attacker] == false

Attack:
  1. attacker calls router.exactInputSingle({pool: curatedPool, recipient: attacker, ...})
  2. router calls pool.swap(attacker, ...) — msg.sender to pool = router
  3. pool calls _beforeSwap(msg.sender=router, ...)
  4. extension checks allowedSwapper[pool][router] == true → passes
  5. swap executes; attacker receives output tokens

Alternatively (DoS path), if router is NOT allowlisted:
  1. allowlisted user calls router.exactInputSingle({pool: curatedPool, ...})
  2. extension checks allowedSwapper[pool][router] == false → reverts NotAllowedToSwap
  3. allowlisted user cannot use the router at all

Foundry test sketch:
  vm.prank(poolAdmin);
  extension.setAllowedToSwap(pool, address(router), true);
  // attacker is not allowlisted
  vm.prank(attacker);
  router.exactInputSingle(ExactInputSingleParams({pool: curatedPool, ...}));
  // assert swap succeeded despite attacker not being allowlisted
```
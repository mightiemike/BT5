Audit Report

## Title
`SwapAllowlistExtension` validates the router intermediary instead of the actual swapper, allowing any user to bypass the curated-pool allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to enable router-mediated swaps for permitted users simultaneously grants every address on the network the ability to bypass the allowlist entirely.

## Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap()` forwards that value verbatim as the first positional argument of the extension call:

```solidity
// metric-core/contracts/ExtensionCalling.sol L149-177
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router itself `msg.sender` of that call:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

The same pattern applies to `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165). The actual user's address (`msg.sender` of the router call) is never visible to the guard. The extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a named set of addresses. To let those addresses use the canonical router, the admin must call `setAllowedToSwap(pool, address(router), true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller of the router, including addresses the admin explicitly never allowlisted. The allowlist guard is completely neutralised for router-mediated swaps. Any user can execute swaps at oracle prices in the curated pool, bypassing the curation policy entirely. This constitutes an admin-boundary break where an unprivileged path (calling the public router) circumvents a pool admin's access control, with direct loss of the curated pool's intended swap restriction and potential fund impact to LPs who deposited under the assumption of a restricted swapper set.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical periphery swap entry point. Pool admins who deploy curated pools with `SwapAllowlistExtension` will routinely allowlist the router so their permitted users can swap normally — this is the expected operational pattern. The bypass requires no special privilege, no flash loan, and no multi-step setup: any address calls `exactInputSingle` (or any other router swap function) pointing at the curated pool. The condition is met whenever the router is allowlisted, which is the standard configuration for any pool that intends to support router-mediated swaps for its permitted users.

## Recommendation

The extension must gate the economically relevant actor, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** Add a `swapper` field to `ExactInputSingleParams` (and equivalent structs) that defaults to `msg.sender`. Encode it into `extensionData` and have `SwapAllowlistExtension.beforeSwap()` decode and check it when present, falling back to `sender` for direct pool calls.

2. **Alternatively, check `recipient` instead of `sender`.** For direct swaps the recipient is often the user; however this is also spoofable via a custom recipient, so option 1 is preferred.

The cleanest long-term fix is for the pool to expose a separate `originalCaller` field (analogous to ERC-1271's `isValidSignature` checking the actual signer rather than the forwarding contract), so every extension can gate the true economic actor regardless of the call path.

## Proof of Concept

```
Setup
─────
1. Deploy MetricOmmPool with SwapAllowlistExtension (beforeSwap order = 1).
2. Pool admin calls setAllowedToSwap(pool, ALICE, true).
   ALICE is the only permitted swapper.
3. Pool admin calls setAllowedToSwap(pool, address(router), true)
   so ALICE can use the router.

Attack
──────
4. BOB (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: BOB, ...})
5. Router calls pool.swap(BOB, ...) — msg.sender of pool.swap = router.
6. Pool calls extension.beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] → true → passes.
8. BOB's swap executes at oracle price; BOB receives token output.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist bypassed.
```

Foundry test outline: deploy pool with `SwapAllowlistExtension`, allowlist only ALICE and the router, call `exactInputSingle` from BOB's address, assert the swap succeeds (demonstrating the bypass) rather than reverting with `NotAllowedToSwap`.
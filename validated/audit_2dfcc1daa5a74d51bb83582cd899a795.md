Audit Report

## Title
Swap Allowlist Checks Router Address Instead of Actual User, Breaking Access Control for Router-Mediated Swaps - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router address, not the actual user. This makes it impossible to enforce per-user swap access control for router-mediated swaps: either allowlisted users cannot use the router at all, or the pool admin must allowlist the router address, which opens the pool to every user regardless of allowlist status.

## Finding Description
**Call path:**
`MetricOmmSimpleRouter.exactInputSingle()` → `IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` → `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` where `msg.sender` = router → `ExtensionCalling._beforeSwap` encodes `sender = router_address` → `SwapAllowlistExtension.beforeSwap(sender=router_address, ...)` checks `allowedSwapper[pool][router_address]`.

In `MetricOmmPool.swap()`:
```solidity
_beforeSwap(
  msg.sender,   // <-- router address when called via router
  recipient,
  ...
);
```

In `SwapAllowlistExtension.beforeSwap`:
```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

`sender` is the router address, not the original user. The router stores the original user only in transient storage for payment purposes (`_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, msg.sender, tokenIn)`) but never passes it to the pool's `swap()` call.

**Contrast with `DepositAllowlistExtension`:** that extension correctly checks `owner` (the position owner, passed as a distinct parameter) rather than `sender` (the msg.sender of `addLiquidity`), achieving true per-user gating. No equivalent user-identity parameter exists in the pool's `swap()` signature.

**Two failure modes for any pool with `SwapAllowlistExtension` active:**

1. **Allowlist bypass:** Pool admin allowlists the router address so that allowlisted users can swap via the router. Any unprivileged user can now call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` through the router and pass the allowlist check, because the extension sees `sender = router` which is allowlisted.

2. **Broken core swap flow:** Pool admin allowlists only specific user addresses (not the router). Those allowlisted users cannot swap through the router because the extension sees `sender = router` (not allowlisted) and reverts `NotAllowedToSwap`. The router is the primary supported swap entrypoint, making the allowlist-protected pool's swap flow unusable for its intended users.

Existing guards are insufficient: `BaseMetricExtension.onlyPool` only verifies the caller is a registered pool; it does not recover the original user identity. The router's transient callback context (`TransientCallbackPool`) is inaccessible to the extension.

## Impact Explanation
Broken core pool swap functionality: the swap allowlist cannot enforce per-user access control for router-mediated swaps. In failure mode 1, an unprivileged trader bypasses a curated pool's access control entirely. In failure mode 2, the allowlist-protected pool's primary swap path is unusable for its intended allowlisted users. Both outcomes directly contradict the purpose of deploying `SwapAllowlistExtension`.

## Likelihood Explanation
Any unprivileged user can attempt router-mediated swaps on an allowlist-protected pool. Failure mode 2 is triggered immediately when any allowlisted user attempts to use the router. Failure mode 1 is triggered whenever the pool admin allowlists the router to work around mode 2. Both are reachable with zero special preconditions beyond the pool having `SwapAllowlistExtension` configured.

## Recommendation
Pass the original user identity through the swap path. Options include: (a) add an explicit `user` parameter to `IMetricOmmPoolActions.swap()` that the router populates with `msg.sender` before calling the pool, and have `SwapAllowlistExtension` check that field; or (b) have the router write the original caller into a known transient slot that the extension can read. The deposit allowlist's pattern of checking a dedicated identity parameter (`owner`) rather than `sender` should be replicated for swaps.

## Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Pool admin allowlists router: swapExtension.setAllowedToSwap(pool, address(router), true)
// 3. Non-allowlisted attacker calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    recipient: attacker,
    amountIn: 1000,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Pool calls _beforeSwap(msg.sender=router, ...)
// Extension checks allowedSwapper[pool][router] == true → passes
// Attacker swaps successfully despite not being on the allowlist
```
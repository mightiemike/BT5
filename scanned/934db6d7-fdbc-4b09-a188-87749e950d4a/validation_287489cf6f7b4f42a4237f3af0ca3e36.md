### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the pool to every unprivileged caller, because the extension cannot distinguish between allowlisted and non-allowlisted users once the router is the `sender`.

### Finding Description

In `SwapAllowlistExtension.beforeSwap`, the guard is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct), and `sender` is the first argument forwarded by the pool. [1](#0-0) 

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap(...)`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

Therefore `sender` arriving at the extension is the **router address**, not the actual user. The allowlist lookup becomes `allowedSwapper[pool][router]`. If the pool admin allowlists the router (the natural step to let their curated users access the router's UX — multi-hop, exact-output, deadline, etc.), the check passes for **every** caller who routes through the router, regardless of whether they are individually allowlisted.

This is the direct analog of the external `split_off_older_than` bug: the partition is applied to the wrong entity. The extension is supposed to partition callers into *allowed* vs *blocked* based on the actual user identity, but instead partitions on the intermediary (router vs. non-router), inverting the intended gate for all router-mediated flows.

### Impact Explanation
Any unprivileged user can bypass the swap allowlist on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`). The pool's curation policy is completely nullified for router-mediated paths. Unauthorized users gain access to swap at the pool's oracle-anchored prices, which may be favorable relative to the market, constituting a direct loss of LP value and a broken core pool invariant (the allowlist).

### Likelihood Explanation
Medium. The bypass requires the pool admin to have allowlisted the router address. This is a natural and expected configuration: any pool admin who wants their curated users to benefit from the router's features (multi-hop routing, exact-output, slippage protection) must allowlist the router. There is no alternative path — the extension provides no mechanism to allow specific users through the router without also allowing all users. The pool admin's action is not malicious; the design flaw makes the bypass unavoidable once router access is desired.

### Recommendation
The extension must check the **actual user**, not the intermediary. Options:

1. Have `MetricOmmSimpleRouter` encode the real `msg.sender` into `extensionData` and have the extension decode and verify it (requires trust in the router, which is a known, factory-registered contract).
2. Add a router-aware forwarding field to the pool's swap interface so the actual originator is always available to extensions.
3. Document clearly that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and remove the router from the supported periphery for allowlisted pools.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` registered as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is intended to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to let `userA` use the router.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — `msg.sender` of the pool call = router.
6. Pool calls `_beforeSwap(router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true`.
8. `userB`'s swap executes successfully — allowlist fully bypassed. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );

```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

The call chain is fully traceable. Here is the exact analysis:

**Step 1 — `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:** [1](#0-0) 

**Step 2 — `MetricOmmSimpleRouter.exactInput` calls `pool.swap` from the router itself for every hop:** [2](#0-1) 

For every hop, `IMetricOmmPoolActions(pool).swap(...)` is called with `msg.sender = router`. The `_setNextCallbackContext` payer tracking is separate from the `sender` the pool sees.

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender = pool` and `sender = router`:** [3](#0-2) 

The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`. The original user identity is never forwarded to the extension.

---

### Title
SwapAllowlistExtension checks router address instead of original user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument passed by the pool — which is `msg.sender` of `pool.swap`. When `MetricOmmSimpleRouter` executes any swap (single-hop or multi-hop), it is the direct caller of `pool.swap`, so `sender = router` for every hop. If a pool admin allowlists the router address (`allowedSwapper[pool][router] = true`), every user who routes through the router passes the check regardless of their own allowlist status.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(msg.sender, recipient, ...);   // MetricOmmPool.sol:231
```

`ExtensionCalling._beforeSwap` forwards this unchanged to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))  // ExtensionCalling.sol:165
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap`. When `MetricOmmSimpleRouter.exactInput` runs its hop loop, it calls `pool.swap(...)` directly — so `sender = address(router)` for every hop. The original `msg.sender` of `exactInput` (the attacker) is never visible to the extension.

A pool admin who wants to permit router-based swaps while still gating individual users will naturally call `setAllowedToSwap(pool, router, true)`. Once that is set, any address — including one explicitly excluded from the allowlist — can swap through every pool in a multi-hop path by calling `router.exactInput`.

### Impact Explanation

The `SwapAllowlistExtension` is the sole mechanism for pool-level swap curation. When the router is allowlisted, the extension provides zero protection against unauthorized swappers who route through the router. All curated pools in a multi-hop path are simultaneously bypassed in a single transaction. This breaks the core pool functionality the extension is designed to enforce.

### Likelihood Explanation

Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and also wants to support router-based swaps will allowlist the router — this is the natural and expected configuration. The bypass requires no special privileges beyond calling the public `router.exactInput` function.

### Recommendation

The extension cannot recover the original user identity from the `sender` argument alone because the pool only passes `msg.sender`. Two options:

1. **Document the limitation clearly**: State that `SwapAllowlistExtension` gates the immediate caller of `pool.swap`, not the end user. Pool admins must allowlist individual users and require them to call the pool directly, not through the router.
2. **Pass original user identity through `extensionData`**: Have the router encode `msg.sender` into `extensionData` for each hop, and have the extension decode and check it. This requires a coordinated change to both the router and the extension.

### Proof of Concept

```solidity
// Setup: poolA and poolB both have SwapAllowlistExtension configured
// allowedSwapper[poolA][router] = true
// allowedSwapper[poolB][router] = true
// allowedSwapper[poolA][attacker] = false  (never set)
// allowedSwapper[poolB][attacker] = false  (never set)

// Attacker calls:
router.exactInput(ExactInputParams({
    tokens: [tokenA, tokenB, tokenC],
    pools: [poolA, poolB],
    ...
}));
// For hop 0: poolA.swap is called with msg.sender = router
//   → beforeSwap(sender=router) → allowedSwapper[poolA][router] = true → PASSES
// For hop 1: poolB.swap is called with msg.sender = router
//   → beforeSwap(sender=router) → allowedSwapper[poolB][router] = true → PASSES
// Attacker completes the swap despite being individually blocked on both pools.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```

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

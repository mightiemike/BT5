The call chain is fully traceable from the code. Here is the analysis:

**Full call path:**

1. `user (non-allowlisted)` calls `MetricOmmSimpleRouter.exactInput()`
2. Router calls `pool.swap(recipient, ...)` — here `msg.sender` to the pool is the **router address**
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` — `msg.sender` = router
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to `SwapAllowlistExtension.beforeSwap(sender=router, ...)`
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`

**The check in `SwapAllowlistExtension`:** [1](#0-0) 

`msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument — which is `msg.sender` of `pool.swap()`, i.e., the router.

**The pool passes `msg.sender` as `sender` to `_beforeSwap`:** [2](#0-1) 

**The router calls `pool.swap()` directly (no original user identity forwarded):** [3](#0-2) 

---

**Verdict: This is a real vulnerability.**

---

### Title
`SwapAllowlistExtension` Bypassed via Router — Any User Can Swap Through Allowlisted Router Address - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension` gates swaps by checking the `sender` argument passed to `beforeSwap`, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the `sender` seen by the extension is always the **router address**, not the original user. If the pool admin allowlists the router (a natural configuration to enable router-mediated swaps), every user — including non-allowlisted ones — can bypass the per-user gate by calling `exactInput` or `exactInputSingle` through the router.

### Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [4](#0-3) 

`ExtensionCalling._beforeSwap` forwards this unchanged to the extension: [5](#0-4) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`: [6](#0-5) 

When `MetricOmmSimpleRouter.exactInput()` is called, the router calls `pool.swap()` directly — there is no mechanism to forward the original `msg.sender`: [7](#0-6) 

So `sender` at the extension is always the router address. If `allowedSwapper[pool][router] == true`, the check passes for **every** user who routes through the router, regardless of whether that user is individually allowlisted.

### Impact Explanation
Any user can bypass a per-user swap allowlist on any pool that has allowlisted the router. The pool admin's intent to restrict swaps to specific addresses is silently violated. Unauthorized users can trade in pools intended to be private or restricted (e.g., institutional pools, KYC-gated pools, or pools with restricted counterparties). This is broken core access-control functionality.

### Likelihood Explanation
Allowlisting the router is the natural and expected configuration for any pool that wants to support router-mediated swaps while still restricting direct `pool.swap()` calls. The bypass requires no special privileges — any public user can call `exactInput` on the router.

### Recommendation
The router should forward the original `msg.sender` as part of the `extensionData` or a dedicated field, and `SwapAllowlistExtension` should extract and check the originating user when the caller is a trusted router. Alternatively, the extension should check both `sender` and an optional `origin` field, or the pool interface should carry an `origin` address through the hook arguments.

### Proof of Concept
```solidity
// Two pools each with SwapAllowlistExtension
// Admin allowlists only the router on both pools
swapExtension.setAllowedToSwap(address(pool1), address(router), true);
swapExtension.setAllowedToSwap(address(pool2), address(router), true);

// Non-allowlisted user calls exactInput through the router
vm.prank(nonAllowlistedUser);
router.exactInput(ExactInputParams({
    tokens: [tokenA, tokenB, tokenC],
    pools: [pool1, pool2],
    ...
}));
// Both hops succeed — sender seen by extension is router, which is allowlisted
// nonAllowlistedUser was never individually allowlisted but swapped successfully
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-175)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
```

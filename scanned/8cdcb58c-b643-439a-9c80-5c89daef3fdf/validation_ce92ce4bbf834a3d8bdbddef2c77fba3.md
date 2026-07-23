### Title
Router-Mediated Swaps Corrupt Swapper Identity in `SwapAllowlistExtension::beforeSwap`, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` from the pool's perspective. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the original user. This means the allowlist checks the router's identity, not the actual swapper's. Any pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the pool to **all** users, defeating the per-user allowlist entirely.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   [msg.sender = router]
           → _beforeSwap(msg.sender=router, ...)
             → SwapAllowlistExtension.beforeSwap(sender=router, ...)
               → allowedSwapper[pool][router]  ← checks router, not user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

When the router calls `pool.swap()`, it passes no original-user identity — only its own address appears as `sender`: [4](#0-3) 

**Two broken outcomes result:**

1. **Allowlist bypass (security impact):** If the pool admin allowlists the router address (`allowedSwapper[pool][router] = true`) to permit router-mediated swaps, every user — including those explicitly not allowlisted — can swap through the router and pass the gate. The per-user restriction is completely nullified.

2. **Allowlisted users locked out of router (functionality impact):** If the admin does not allowlist the router, allowlisted users cannot use the router at all, even though they are individually permitted. The router is unusable for any allowlisted pool.

The `whenNotPaused` modifier fires before `_beforeSwap`, so the pause path is not a factor here — the hook is never reached when the pool is paused: [5](#0-4) 

---

### Impact Explanation

The `SwapAllowlistExtension` is designed to restrict swaps to a curated set of addresses per pool. Router-mediated swaps structurally corrupt the identity the hook checks. A pool admin who enables router access by allowlisting the router address inadvertently grants swap access to every user on-chain. This is broken core functionality: the allowlist extension cannot enforce its stated invariant for any router-mediated swap path.

---

### Likelihood Explanation

Any pool that (a) uses `SwapAllowlistExtension` and (b) wants to support the official `MetricOmmSimpleRouter` must allowlist the router. This is the natural and expected configuration. The bypass is therefore triggered by normal, intended usage of the protocol's own periphery contracts.

---

### Recommendation

Pass the **original user's address** through the call chain rather than `msg.sender` at the pool boundary. One approach: the router encodes the originating user in `extensionData`, and `SwapAllowlistExtension.beforeSwap` reads it from there — but only when `msg.sender` (the pool) confirms the caller is a trusted router. Alternatively, the pool could accept an explicit `originator` parameter that the router populates, and the extension checks that instead of `sender`.

---

### Proof of Concept

```solidity
// Pool admin sets up a per-user allowlist
swapAllowlist.setAllowedToSwap(pool, alice, true);
// Bob is NOT allowlisted

// Admin allowlists the router to enable router swaps for alice
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// Bob (not allowlisted) calls the router
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    tokenIn: token0,
    amountIn: 1e18,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// ✓ Succeeds — allowedSwapper[pool][router] = true
// Bob bypassed the per-user allowlist entirely
``` [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-224)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

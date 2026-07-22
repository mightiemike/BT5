The call path is fully traceable from the code. Let me confirm the exact binding before rendering a verdict.

**Call chain:**

1. EOA → `MetricOmmSimpleRouter.exactInputSingle()` — `msg.sender` = EOA
2. Router → `pool.swap(params.recipient, ...)` — router is now `msg.sender` to the pool
3. Pool passes `msg.sender` (= router) as `sender` to `_beforeSwap()`
4. `_beforeSwap` encodes and dispatches `sender` = router to `SwapAllowlistExtension.beforeSwap()`
5. Extension checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`

The `sender` the extension sees is always the router, never the originating EOA. [1](#0-0) [2](#0-1) 

---

### Title
Router-mediated swaps expose `SwapAllowlistExtension` to full per-user bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the value passed by the pool — always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the originating EOA. Any pool admin who allowlists the router address (the only way to permit router-mediated swaps) simultaneously grants every unprivileged EOA the ability to bypass the per-user allowlist.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`_beforeSwap` forwards this value verbatim to every configured extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. The originating EOA is never inspected.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the originating caller:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
  );
``` [3](#0-2) 

A pool admin who wants to support router-mediated swaps for allowlisted users must add the router to `allowedSwapper`. Doing so unconditionally opens the pool to every EOA that calls `exactInputSingle`, `exactInput`, or `exactOutputSingle`.

### Impact Explanation

The `SwapAllowlistExtension` is the sole mechanism for curated pools to restrict who may trade. Once the router is allowlisted (the only way to enable router-mediated swaps), the allowlist provides zero per-user protection. Any EOA can execute swaps on a pool that was intended to be restricted. This is broken core functionality of the extension system.

### Likelihood Explanation

Any pool that (a) deploys with `SwapAllowlistExtension` and (b) intends to support the public router will trigger this condition. The router is a first-class supported periphery contract; pool admins have no documented warning that allowlisting it collapses per-user gating. The bypass requires no special privileges — any EOA with token approval can exploit it.

### Recommendation

Pass the originating caller through the swap path so the extension can gate on the true economic actor. Two options:

1. **Encode originating caller in `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. The extension decodes and checks this value. This requires the extension to trust the router as a forwarding agent, which must be explicitly configured.

2. **Add a `swapFor(address onBehalfOf, ...)` entry point on the pool**: The pool accepts an explicit `onBehalfOf` address and passes it as `sender` to extensions, while still requiring `msg.sender` to be an approved forwarder. This is the cleanest fix but requires a core interface change.

Until fixed, pool admins should be warned that allowlisting the router address disables per-user swap gating entirely.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_routerBypassesSwapAllowlist() public {
    // Pool admin allowlists ONLY the router (required for router swaps)
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Non-allowlisted EOA swaps through the router — succeeds
    vm.prank(nonAllowlistedEOA);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: nonAllowlistedEOA,
        deadline: type(uint256).max,
        priceLimitX64: 0,
        extensionData: ""
    }));

    // Same EOA calling pool.swap directly — reverts with NotAllowedToSwap
    vm.prank(nonAllowlistedEOA);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    pool.swap(nonAllowlistedEOA, true, 1000, 0, "", "");
}
``` [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-41)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

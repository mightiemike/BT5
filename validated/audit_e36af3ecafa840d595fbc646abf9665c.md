### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the actual user, allowing any caller to bypass the swap allowlist via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router address**, not the actual user. If the router is allowlisted as a swapper (a natural configuration for curated pools that want to support router-based trading), any non-allowlisted user can bypass the curation policy by routing through the router.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`).
2. The router calls `pool.swap(recipient, zeroForOne, amountSpecified, priceLimitX64, "", extensionData)` — the pool's `msg.sender` is the **router address**.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, passing the router address as `sender`. [1](#0-0) 

4. `ExtensionCalling._beforeSwap()` encodes and forwards `sender` (= router) to the extension. [2](#0-1) 

5. `SwapAllowlistExtension.beforeSwap()` checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` = pool (correct), but `sender` = **router address**, not the actual EOA. The extension has no visibility into who called the router.

**The router never forwards the actual user identity to the pool:** [4](#0-3) 

The actual user (`msg.sender` of the router call) is stored only in the transient callback context for payment settlement — it is never passed to `pool.swap()` as the `sender` argument.

---

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` to restrict swaps to specific addresses, and who also allowlists the router address (so that allowlisted users can trade through the official periphery), inadvertently opens the pool to **all users**. Any non-allowlisted address can call `router.exactInputSingle()` and the extension will see `sender = router`, which is allowlisted, and pass the check. The curation policy is completely bypassed, allowing unauthorized users to swap against the pool's liquidity at oracle prices.

This is a direct loss-of-curation-control impact: the pool's LP assets are exposed to swappers the admin explicitly intended to exclude.

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router address. This is a natural and expected configuration: a curated pool that wants to support router-based trading for its allowlisted users must allowlist the router. The protocol provides no mechanism to distinguish "router called by an allowlisted user" from "router called by anyone." The tests for `SwapAllowlistExtension` only exercise direct pool calls, not the router path, so this gap is untested. [5](#0-4) 

---

### Recommendation

The `sender` parameter passed to extension hooks must represent the **economic actor** (the end user), not the intermediary contract. Two approaches:

1. **Router-side**: The router encodes the actual `msg.sender` into `extensionData` and the extension decodes it. This requires a convention between the router and extensions.
2. **Extension-side**: `SwapAllowlistExtension` should check `recipient` (the address receiving output tokens) in addition to or instead of `sender`, since `recipient` is set by the actual user and is harder to spoof. However, `recipient` can be a third party, so this is not a clean fix either.
3. **Protocol-level**: Add a dedicated `originator` field to the pool's `swap()` signature that the router populates with `msg.sender`, and pass it through the hook chain as a distinct parameter from `sender`.

Until fixed, pool admins must **not** allowlist the router address on curated pools, and must require users to call `pool.swap()` directly.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists the router so allowlisted users can trade through it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker: a non-allowlisted EOA
address attacker = makeAddr("attacker");
token0.mint(attacker, 10_000);
vm.prank(attacker);
token0.approve(address(router), 10_000);

// Attacker routes through the router — extension sees sender=router (allowlisted), passes
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:             address(pool),
    tokenIn:          address(token0),
    tokenOut:         address(token1),
    zeroForOne:       true,
    amountIn:         1_000,
    amountOutMinimum: 0,
    recipient:        attacker,
    deadline:         block.timestamp + 1,
    priceLimitX64:    0,
    extensionData:    ""
}));
// Swap succeeds — attacker bypassed the allowlist
```

The pool's `_beforeSwap` receives `sender = address(router)`. The extension checks `allowedSwapper[pool][router]` = `true` and does not revert. The attacker, who was never individually allowlisted, completes the swap. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
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
    );
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```

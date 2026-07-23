### Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing any unprivileged caller to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap` function sets `sender = msg.sender = router`. The extension therefore checks the router's address against the allowlist, not the actual user's address. If the pool admin allowlists the router (a natural configuration to enable router-mediated swaps for their curated pool), every unprivileged user can bypass the per-user allowlist by routing through the public router contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` inside the pool for every hop: [4](#0-3) 

The same pattern holds for `exactInput` (all hops) and `exactOutput` (all recursive hops): [5](#0-4) 

The router stores the original `msg.sender` only in transient storage for the payment callback — it is never forwarded to the pool or to any extension: [6](#0-5) 

There is no mechanism in the pool's `swap` signature or in `ExtensionCalling` to carry the original user's identity through to the extension. The extension interface itself only receives `sender` as a positional argument: [7](#0-6) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router address — a natural step to enable router-mediated swaps for their approved users — inadvertently opens the pool to every user of the public router. Any address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) targeting the restricted pool. The extension sees `sender = router`, which is allowlisted, and the swap proceeds. The per-user allowlist is completely bypassed. Unauthorized traders can drain the pool's liquidity at oracle-quoted prices, directly harming LP principal in a pool that was configured to restrict access.

---

### Likelihood Explanation

The `SwapAllowlistExtension` is documented as "Gates `swap` by swapper address, per pool." A pool admin who reads this and wants to support router-mediated swaps for their allowlisted users will naturally allowlist the router address, believing the extension will still gate by individual user identity. The router is a canonical, publicly deployed periphery contract. Allowlisting it is a foreseeable and reasonable configuration mistake. Once the router is allowlisted, the bypass requires no special privilege — any EOA can call the router.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the economically relevant actor, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Extension-side**: `SwapAllowlistExtension` should require callers to supply the actual user address in `extensionData` and verify it against the allowlist, or the pool should expose a dedicated "original initiator" field that periphery contracts populate.

2. **Router-side**: `MetricOmmSimpleRouter` should encode the original `msg.sender` into the `extensionData` it forwards to the pool, so allowlist extensions can extract and verify the real user. The extension can then decode and check the actual initiator rather than the router address.

Until fixed, pool admins must not allowlist the router address on pools that intend per-user access control.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool:
//   extension = SwapAllowlistExtension
//   allowedSwapper[pool][router] = true   ← admin allowlists router to support router swaps
//   allowedSwapper[pool][alice]  = false  ← alice is NOT individually allowlisted

// Alice (not allowlisted) bypasses the guard:
vm.prank(alice);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(restrictedPool),
        recipient:       alice,
        zeroForOne:      true,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:   0,
        deadline:        block.timestamp,
        tokenIn:         token0,
        extensionData:   ""
    })
);
// SwapAllowlistExtension.beforeSwap receives sender = address(router)
// allowedSwapper[pool][router] == true  → no revert
// Alice swaps successfully despite not being individually allowlisted
``` [3](#0-2) [8](#0-7) [9](#0-8)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```

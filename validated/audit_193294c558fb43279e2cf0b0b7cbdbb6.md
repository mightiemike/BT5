### Title
SwapAllowlistExtension Checks Immediate Caller (Router) Instead of Actual Swapper, Enabling Allowlist Bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is `msg.sender` of the `pool.swap` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the router's address, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps for their curated pool inadvertently opens the pool to every user, defeating the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` of that call. The original user's address is stored only in transient callback context and is never forwarded to the pool or the extension: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. In every router path, `pool.swap` sees `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants allowlisted users to be able to use the standard periphery must allowlist the router address. Once the router is allowlisted, the condition `allowedSwapper[pool][router] == true` passes for every caller of the router, regardless of whether that caller is individually allowlisted. Every non-allowlisted user can bypass the curation policy by routing through `MetricOmmSimpleRouter`. The allowlist provides no protection for router-mediated swaps.

The converse is equally broken: if the pool admin does not allowlist the router, individually allowlisted users cannot use the router at all, making the standard periphery unusable for the pool.

---

### Likelihood Explanation

Any pool admin who deploys a curated pool and also wants to support the standard router will naturally allowlist the router address. This is the expected operational configuration. The bypass is then reachable by any unprivileged user with no special setup beyond calling the public router.

---

### Recommendation

The router should forward the originating user's address to the pool so extensions can gate on the economically relevant actor. One approach is to add an optional `originator` field to the swap call or to the `extensionData` payload that the router populates with `msg.sender`, and have `SwapAllowlistExtension` read and verify that field when the immediate caller is a known router. Alternatively, the extension can maintain a separate mapping of trusted routers and, when `sender` is a trusted router, extract the originator from a signed or router-attested field in `extensionData`.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls extension.setAllowedToSwap(pool, alice, true)
   — Alice is the only intended swapper.
3. Pool admin calls extension.setAllowedToSwap(pool, router, true)
   — Required so Alice can use the standard router.
4. Bob (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: pool, recipient: bob, zeroForOne: true,
           amountIn: X, ...
       })
5. Router executes pool.swap(bob, true, X, ...) with msg.sender = router.
6. ExtensionCalling._beforeSwap passes sender = router to SwapAllowlistExtension.
7. Extension evaluates: allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes successfully despite never being allowlisted.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
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

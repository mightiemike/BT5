### Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the originating user. Any pool admin who allowlists the router (required to enable router-mediated swaps for their allowlisted users) simultaneously opens the allowlist to every user on the network.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle(pool, ...)
         → IMetricOmmPoolActions(pool).swap(recipient, ..., extensionData)
              [msg.sender = router]
         → MetricOmmPool._beforeSwap(msg.sender=router, ...)
         → ExtensionCalling._callExtensionsInOrder(BEFORE_SWAP_ORDER, ...)
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              checks allowedSwapper[pool][router]   ← wrong actor
```

**Pool `swap` passes its own `msg.sender` as `sender` to the extension:** [1](#0-0) 

**`ExtensionCalling._beforeSwap` forwards that value unchanged:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` keys the allowlist on `sender` (= router) and `msg.sender` (= pool):** [3](#0-2) 

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly — the router becomes `msg.sender` to the pool:** [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Contrast with `DepositAllowlistExtension`**, which correctly gates on `owner` (the position owner passed explicitly by the caller, not the intermediary contract): [6](#0-5) 

The deposit extension is not affected because `owner` is an explicit argument that survives router/adder indirection. The swap extension is broken because it uses `sender`, which collapses to the router address on every router-mediated swap.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma:

| Router allowlisted? | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| No | **Blocked** (DoS for legitimate users) | Blocked |
| Yes | Allowed | **Allowed — bypass** |

If the admin allowlists the router to enable legitimate router-mediated swaps, every address on the network can bypass the allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The pool's curated access control is completely defeated. Depending on the pool's pricing model and LP composition, unauthorized swappers can extract value from LPs who deposited under the assumption that only vetted counterparties would trade against them.

**Impact: High** — direct bypass of a configured access-control guard with fund-impacting consequences for LPs on curated pools.

---

### Likelihood Explanation

**High.** The router is the standard, documented periphery entry point for swaps. Pool admins who want their allowlisted users to be able to use the router (the normal UX path) must allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle` on the router pointing at the curated pool.

---

### Recommendation

The extension must recover the original user identity rather than trusting the `sender` argument, which is the immediate pool caller. Two sound approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before forwarding to the pool. The extension decodes and verifies it. This requires a trusted router or a signed proof.

2. **Gate on `recipient` instead of `sender` for exact-output flows, and require direct pool calls for allowlisted pools**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory/extension-config validation layer.

3. **Preferred — check both `sender` and `recipient`**: Require that both the caller (`sender`) and the output recipient are allowlisted, so routing through an allowlisted router does not grant access to a non-allowlisted recipient.

---

### Proof of Concept

```solidity
// Setup: pool admin deploys pool with SwapAllowlistExtension
// Admin allowlists alice (legitimate user) and the router (to let alice use the UI)
swapAllowlist.setAllowedToSwap(pool, alice, true);
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// Attack: bob (not allowlisted) bypasses the guard via the router
// The extension sees sender = address(router), which IS allowlisted → passes
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        recipient: bob,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        tokenIn: token0,
        extensionData: ""
    })
);
// bob successfully swaps on a pool he was never allowlisted for
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
